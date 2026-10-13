"""Concurrency-safe, in-memory seat hold engine."""

from __future__ import annotations

import itertools
import threading


class SeatError(Exception):
    """Base class for all seat engine errors."""


class SeatHeldError(SeatError):
    """Seat is held by another user."""


class SeatSoldError(SeatError):
    """Seat is already sold."""


class UserAtLimitError(SeatError):
    """User is at the active-hold limit."""


class UnknownHoldError(SeatError):
    """Hold ID is unknown."""


class ExpiredHoldError(SeatError):
    """Hold expired and seat is not revivable (no longer FREE)."""


class DuplicateSeatsError(SeatError):
    """Duplicate seat IDs in a single hold_many request."""


class FakeClock:
    """Injectable clock for tests."""

    def __init__(self, start: float = 0.0):
        self._t = float(start)

    def now(self) -> float:
        """Return current fake time."""
        return self._t

    def advance(self, seconds: float) -> None:
        """Advance fake time by seconds."""
        self._t += float(seconds)


class SeatEngine:
    """In-memory seat hold engine. One global lock guards every public method."""

    MAX_ACTIVE_HOLDS = 4

    def __init__(self, clock=None):
        self._clock = clock if clock is not None else FakeClock()
        self._lock = threading.Lock()
        self._holds = {}  # hold_id -> {seat_id, user_id, expires_at}
        self._seat_hold = {}  # seat_id -> hold_id (active or expired-but-unreaped)
        self._sold = {}  # seat_id -> user_id
        self._ids = itertools.count(1)

    # -- internals (call only with lock held) --
    def _expire_if_needed(self, seat_id) -> None:
        hold_id = self._seat_hold.get(seat_id)
        if hold_id is None:
            return
        hold = self._holds.get(hold_id)
        if hold is None:
            del self._seat_hold[seat_id]
            return
        if hold["expires_at"] <= self._clock.now():
            # lazy expiry: drop mapping; keep hold record so confirm can apply D1
            del self._seat_hold[seat_id]

    def _active_holds_for(self, user_id) -> int:
        now = self._clock.now()
        return sum(
            1
            for h in self._holds.values()
            if h["user_id"] == user_id
            and h["expires_at"] > now
            and self._seat_hold.get(h["seat_id"]) == h["hold_id"]
        )

    def _is_active(self, hold) -> bool:
        return (
            hold["expires_at"] > self._clock.now()
            and self._seat_hold.get(hold["seat_id"]) == hold["hold_id"]
        )

    # -- public API --
    def hold(self, seat_id, user_id, ttl_seconds) -> str:
        """Hold a FREE seat; idempotent re-hold by same holder (D2); expired re-hold is a new claim if FREE (D3)."""
        with self._lock:
            self._expire_if_needed(seat_id)
            if seat_id in self._sold:
                raise SeatSoldError(f"seat {seat_id} sold")
            existing = self._seat_hold.get(seat_id)
            if existing is not None:
                h = self._holds[existing]
                if h["user_id"] == user_id and h["expires_at"] > self._clock.now():
                    return existing  # D2
                raise SeatHeldError(f"seat {seat_id} held by another user")
            if self._active_holds_for(user_id) >= self.MAX_ACTIVE_HOLDS:
                raise UserAtLimitError(f"user {user_id} at limit")
            hold_id = f"h{next(self._ids)}"
            hold = {
                "hold_id": hold_id,
                "seat_id": seat_id,
                "user_id": user_id,
                "expires_at": self._clock.now() + ttl_seconds,
            }
            self._holds[hold_id] = hold
            self._seat_hold[seat_id] = hold_id
            return hold_id

    def confirm(self, hold_id) -> None:
        """Confirm a hold; expired hold succeeds only if seat still FREE, else ExpiredHoldError (D1)."""
        with self._lock:
            hold = self._holds.get(hold_id)
            if hold is None:
                raise UnknownHoldError(f"unknown hold {hold_id}")
            seat_id = hold["seat_id"]
            self._expire_if_needed(seat_id)
            if self._seat_hold.get(seat_id) == hold_id:
                # active hold: confirm it
                del self._seat_hold[seat_id]
                del self._holds[hold_id]
                self._sold[seat_id] = hold["user_id"]
                return
            # hold not active (expired and reaped, or superseded)
            if seat_id in self._sold or self._seat_hold.get(seat_id) is not None:
                raise ExpiredHoldError(f"hold {hold_id} expired and not revivable")
            # seat FREE: late confirm succeeds (D1), no new TTL
            del self._holds[hold_id]
            self._sold[seat_id] = hold["user_id"]

    def release(self, hold_id) -> None:
        """Release an active hold; releasing an expired hold is a no-op success."""
        with self._lock:
            hold = self._holds.get(hold_id)
            if hold is None:
                raise UnknownHoldError(f"unknown hold {hold_id}")
            seat_id = hold["seat_id"]
            self._expire_if_needed(seat_id)
            if self._seat_hold.get(seat_id) == hold_id:
                del self._seat_hold[seat_id]
            self._holds.pop(hold_id, None)

    def seat_status(self, seat_id):
        """Return FREE | HELD(user, expires_at) | SOLD(user); applies lazy expiry on read."""
        with self._lock:
            if seat_id in self._sold:
                return ("SOLD", self._sold[seat_id])
            self._expire_if_needed(seat_id)
            hold_id = self._seat_hold.get(seat_id)
            if hold_id is None:
                return ("FREE",)
            h = self._holds[hold_id]
            return ("HELD", h["user_id"], h["expires_at"])

    def hold_many(self, user_id, seat_ids, ttl_seconds):
        """All-or-nothing multi-hold; rejects duplicates; limit counts only newly held seats (D4/D5)."""
        with self._lock:
            seat_ids = list(seat_ids)
            if len(set(seat_ids)) != len(seat_ids):
                raise DuplicateSeatsError("duplicate seat IDs")
            # validate phase (no mutation)
            for s in seat_ids:
                self._expire_if_needed(s)
            new_seats = []
            for s in seat_ids:
                if s in self._sold:
                    raise SeatSoldError(f"seat {s} sold")
                existing = self._seat_hold.get(s)
                if existing is not None:
                    h = self._holds[existing]
                    if h["user_id"] == user_id and h["expires_at"] > self._clock.now():
                        continue  # idempotent, not counted (D4)
                    raise SeatHeldError(f"seat {s} held by another user")
                new_seats.append(s)
            if self._active_holds_for(user_id) + len(new_seats) > self.MAX_ACTIVE_HOLDS:
                raise UserAtLimitError(f"user {user_id} at limit")
            # apply phase
            ids = []
            for s in seat_ids:
                existing = self._seat_hold.get(s)
                if existing is not None:
                    ids.append(existing)
                    continue
                hold_id = f"h{next(self._ids)}"
                hold = {
                    "hold_id": hold_id,
                    "seat_id": s,
                    "user_id": user_id,
                    "expires_at": self._clock.now() + ttl_seconds,
                }
                self._holds[hold_id] = hold
                self._seat_hold[s] = hold_id
                ids.append(hold_id)
            return ids
