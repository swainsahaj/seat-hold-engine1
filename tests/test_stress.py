"""Concurrency stress test: 8 threads hammering 5 seats."""

import os
import random
import threading

from seat_engine import ExpiredHoldError, FakeClock, SeatEngine

N_THREADS = 8
N_SEATS = 5
OPS_PER_THREAD = int(os.environ.get("STRESS_OPS", "2000"))


class LockedClock(FakeClock):
    def __init__(self, start=0.0):
        super().__init__(start)
        self._m = threading.Lock()

    def now(self):
        with self._m:
            return super().now()

    def advance(self, seconds):
        with self._m:
            super().advance(seconds)


def base_seed():
    return int(os.environ.get("SEED", "999"))


def test_stress():
    print(f"\nSTRESS base_seed={base_seed()} threads={N_THREADS} ops={OPS_PER_THREAD}")
    clock = LockedClock()
    eng = SeatEngine(clock)
    seats = [f"S{i}" for i in range(N_SEATS)]
    users = ["u1", "u2", "u3"]
    confirmed = []  # (seat, user)
    clock_lock = threading.Lock()
    clog_lock = threading.Lock()

    def worker(tid):
        seed = base_seed() + tid
        print(f"thread-{tid} seed={seed}")
        rng = random.Random(seed)
        live = []
        for _ in range(OPS_PER_THREAD):
            op = rng.choices(
                ["hold", "hold_many", "confirm", "release", "advance"],
                weights=[35, 15, 20, 15, 15],
            )[0]
            try:
                if op == "hold":
                    hid = eng.hold(rng.choice(seats), rng.choice(users), rng.randint(1, 10))
                    live.append(hid)
                elif op == "hold_many":
                    k = rng.randint(1, 3)
                    ids = eng.hold_many(rng.choice(users),
                                        [rng.choice(seats) for _ in range(k)],
                                        rng.randint(1, 10))
                    live.extend(ids)
                elif op == "confirm":
                    if not live:
                        continue
                    hid = rng.choice(live)
                    try:
                        # resolve seat/user before confirm for the log
                        h = eng._holds.get(hid)
                        seat, user = (h["seat_id"], h["user_id"]) if h else (None, None)
                        eng.confirm(hid)
                        if seat is not None:
                            with clog_lock:
                                confirmed.append((seat, user))
                    except Exception:
                        pass
                    live = list(eng._holds.keys())
                elif op == "release":
                    if not live:
                        continue
                    try:
                        eng.release(rng.choice(live))
                    except Exception:
                        pass
                    live = list(eng._holds.keys())
                else:
                    with clock_lock:
                        clock.advance(rng.randint(1, 3))
            except Exception:
                pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariant 1: no seat SOLD to two different users (sold map holds one owner).
    sold = dict(eng._sold)
    seen = {}
    for seat, user in confirmed:
        st = eng.seat_status(seat)
        if st[0] == "SOLD":
            assert st[1] == sold[seat], f"SOLD owner mismatch {seat}"
        if seat in seen:
            assert seen[seat] == user, f"seat {seat} confirmed by two users: {seen[seat]} vs {user}"
        else:
            seen[seat] = user
    # every confirmed seat still SOLD to its confirmer (no steal from expired holds)
    for seat, user in seen.items():
        if seat in sold:
            assert sold[seat] == user, f"seat {seat} sold to {sold[seat]}, confirmed by {user}"

    # Invariant 2: no user holds more than 4 active seats.
    counts = {}
    for s in seats:
        st = eng.seat_status(s)
        if st[0] == "HELD":
            counts[st[1]] = counts.get(st[1], 0) + 1
    for u, c in counts.items():
        assert c <= 4, f"user {u} holds {c} > 4"

    # Invariant 3: no confirmed hold came from an expired hold someone else took.
    # Covered above: each confirmed (seat,user) matches the final SOLD owner;
    # a steal would show a confirm logged for a seat sold to someone else.
    print(f"STRESS done sold={sold} active={counts} confirms={len(confirmed)}")
