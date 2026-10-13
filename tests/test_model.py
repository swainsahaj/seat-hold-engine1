"""Model-based differential test: engine vs single-threaded reference model."""

import os
import random

import pytest

from seat_engine import (
    DuplicateSeatsError,
    ExpiredHoldError,
    FakeClock,
    SeatEngine,
    SeatHeldError,
    SeatSoldError,
    UnknownHoldError,
    UserAtLimitError,
)

N_OPS = 10_000
SEATS = [f"S{i}" for i in range(6)]
USERS = ["u1", "u2", "u3"]
ERR_NAMES = (
    "SeatHeldError",
    "SeatSoldError",
    "UserAtLimitError",
    "UnknownHoldError",
    "ExpiredHoldError",
    "DuplicateSeatsError",
)


def base_seed():
    return int(os.environ.get("SEED", "12345"))


class RefModel:
    """Simple single-threaded reference model using plain dicts, no locks."""

    MAX = 4

    def __init__(self, clock):
        self.c = clock
        self.holds = {}
        self.seat_hold = {}
        self.sold = {}
        self.n = 1

    def _exp(self, s):
        hid = self.seat_hold.get(s)
        if hid is None:
            return
        h = self.holds.get(hid)
        if h is None:
            del self.seat_hold[s]
            return
        if h["expires_at"] <= self.c.now():
            del self.seat_hold[s]

    def _active(self, u):
        now = self.c.now()
        return sum(
            1
            for h in self.holds.values()
            if h["user_id"] == u
            and h["expires_at"] > now
            and self.seat_hold.get(h["seat_id"]) == h["hold_id"]
        )

    def hold(self, seat, user, ttl):
        self._exp(seat)
        if seat in self.sold:
            raise SeatSoldError(seat)
        ex = self.seat_hold.get(seat)
        if ex is not None:
            h = self.holds[ex]
            if h["user_id"] == user and h["expires_at"] > self.c.now():
                return ex
            raise SeatHeldError(seat)
        if self._active(user) >= self.MAX:
            raise UserAtLimitError(user)
        hid = f"h{self.n}"
        self.n += 1
        self.holds[hid] = {"hold_id": hid, "seat_id": seat, "user_id": user,
                           "expires_at": self.c.now() + ttl}
        self.seat_hold[seat] = hid
        return hid

    def confirm(self, hid):
        h = self.holds.get(hid)
        if h is None:
            raise UnknownHoldError(hid)
        s = h["seat_id"]
        self._exp(s)
        if self.seat_hold.get(s) == hid:
            del self.seat_hold[s]
            del self.holds[hid]
            self.sold[s] = h["user_id"]
            return
        if s in self.sold or self.seat_hold.get(s) is not None:
            raise ExpiredHoldError(hid)
        del self.holds[hid]
        self.sold[s] = h["user_id"]

    def release(self, hid):
        h = self.holds.get(hid)
        if h is None:
            raise UnknownHoldError(hid)
        s = h["seat_id"]
        self._exp(s)
        if self.seat_hold.get(s) == hid:
            del self.seat_hold[s]
        del self.holds[hid]

    def status(self, s):
        if s in self.sold:
            return ("SOLD", self.sold[s])
        self._exp(s)
        hid = self.seat_hold.get(s)
        if hid is None:
            return ("FREE",)
        return ("HELD", self.holds[hid]["user_id"])

    def hold_many(self, user, seats, ttl):
        seats = list(seats)
        if len(set(seats)) != len(seats):
            raise DuplicateSeatsError("dup")
        for s in seats:
            self._exp(s)
        new = []
        for s in seats:
            if s in self.sold:
                raise SeatSoldError(s)
            ex = self.seat_hold.get(s)
            if ex is not None:
                h = self.holds[ex]
                if h["user_id"] == user and h["expires_at"] > self.c.now():
                    continue
                raise SeatHeldError(s)
            new.append(s)
        if self._active(user) + len(new) > self.MAX:
            raise UserAtLimitError(user)
        out = []
        for s in seats:
            if self.seat_hold.get(s) is not None:
                out.append(self.seat_hold[s])
                continue
            hid = f"h{self.n}"
            self.n += 1
            self.holds[hid] = {"hold_id": hid, "seat_id": s, "user_id": user,
                               "expires_at": self.c.now() + ttl}
            self.seat_hold[s] = hid
            out.append(hid)
        return out


def norm_status(st):
    # compare kind+user only (ignore absolute expires_at)
    return st[:2]


def outcome(fn):
    try:
        r = fn()
    except Exception as e:
        return ("err", type(e).__name__)
    if isinstance(r, list):
        return ("ok", "list%d" % len(r))
    if isinstance(r, tuple):
        return ("ok", norm_status(r))
    return ("ok", "ok")


def snapshot(eng, ref):
    seats = {s: norm_status(eng.seat_status(s)) for s in SEATS}
    rseats = {s: ref.status(s) for s in SEATS}
    return seats, rseats


def run_differential(seed, n_ops=N_OPS):
    rng = random.Random(seed)
    ce, cr = FakeClock(), FakeClock()
    eng, ref = SeatEngine(ce), RefModel(cr)
    eng_live, ref_live = [], []
    for i in range(n_ops):
        op = rng.choices(
            ["hold", "hold_many", "confirm", "release", "advance"],
            weights=[35, 15, 20, 15, 15],
        )[0]
        if op == "hold":
            s, u, ttl = rng.choice(SEATS), rng.choice(USERS), rng.randint(1, 10)
            re = outcome(lambda: eng.hold(s, u, ttl))
            rr = outcome(lambda: ref.hold(s, u, ttl))
            assert re == rr, f"seed={seed} step={i} hold mismatch {re} vs {rr}"
            if re[0] == "ok":
                eng_live.append(eng._seat_hold[s])
                ref_live.append(ref.seat_hold[s])
        elif op == "hold_many":
            u = rng.choice(USERS)
            k = rng.randint(1, 3)
            seats = [rng.choice(SEATS) for _ in range(k)]
            ttl = rng.randint(1, 10)
            re = outcome(lambda s2=seats: eng.hold_many(u, s2, ttl))
            rr = outcome(lambda s2=seats: ref.hold_many(u, s2, ttl))
            assert re == rr, f"seed={seed} step={i} hold_many mismatch {re} vs {rr} seats={seats}"
            if re[0] == "ok":
                eng_live = list(eng._holds.keys())
                ref_live = list(ref.holds.keys())
        elif op == "confirm":
            if eng_live and ref_live and rng.random() < 0.8:
                j = rng.randrange(min(len(eng_live), len(ref_live)))
                eh, rh = eng_live[j], ref_live[j]
            else:
                eh = rh = f"h{rng.randint(1, 20000)}"
            re = outcome(lambda: eng.confirm(eh))
            rr = outcome(lambda: ref.confirm(rh))
            assert re == rr, f"seed={seed} step={i} confirm mismatch {re} vs {rr} ({eh},{rh})"
            eng_live = list(eng._holds.keys())
            ref_live = list(ref.holds.keys())
        elif op == "release":
            if eng_live and ref_live and rng.random() < 0.8:
                j = rng.randrange(min(len(eng_live), len(ref_live)))
                eh, rh = eng_live[j], ref_live[j]
            else:
                eh = rh = f"h{rng.randint(1, 20000)}"
            re = outcome(lambda: eng.release(eh))
            rr = outcome(lambda: ref.release(rh))
            assert re == rr, f"seed={seed} step={i} release mismatch {re} vs {rr}"
            eng_live = list(eng._holds.keys())
            ref_live = list(ref.holds.keys())
        else:
            dt = rng.randint(1, 5)
            ce.advance(dt)
            cr.advance(dt)
        es, rs = snapshot(eng, ref)
        assert es == rs, f"seed={seed} step={i} op={op} state mismatch {es} vs {rs}"


def test_differential():
    seed = base_seed()
    run_differential(seed)


# ---------------------------------------------------------------------------
# DELIBERATELY BROKEN reference model: 4-seat limit check REMOVED.
# This test is expected to FAIL (xfail): it asserts the broken model matches
# the engine, which it cannot once a user exceeds 4 holds. The failure proves
# the limit rule is load-bearing and that this suite can catch its removal.
# ---------------------------------------------------------------------------
class BrokenNoLimitModel(RefModel):
    def hold(self, seat, user, ttl):
        self._exp(seat)
        if seat in self.sold:
            raise SeatSoldError(seat)
        ex = self.seat_hold.get(seat)
        if ex is not None:
            h = self.holds[ex]
            if h["user_id"] == user and h["expires_at"] > self.c.now():
                return ex
            raise SeatHeldError(seat)
        # BUG: limit check removed
        hid = f"h{self.n}"
        self.n += 1
        self.holds[hid] = {"hold_id": hid, "seat_id": seat, "user_id": user,
                           "expires_at": self.c.now() + ttl}
        self.seat_hold[seat] = hid
        return hid


@pytest.mark.xfail(strict=True, reason="DELIBERATE: broken model without 4-seat limit must diverge")
def test_broken_model_DELIBERATELY_removed_limit_fails():
    ce, cr = FakeClock(), FakeClock()
    eng, ref = SeatEngine(ce), BrokenNoLimitModel(cr)
    seats = ["A1", "A2", "A3", "A4", "A5"]
    for s in seats:
        eng.hold(s, "u1", 100)
        ref.hold(s, "u1", 100)
    # engine must reject the 5th; broken model accepts -> assertion below fails
    assert len(ref.holds) == len(eng._holds), "seed=deliberate broken model should diverge here"
