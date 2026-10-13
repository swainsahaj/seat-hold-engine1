# Seat Hold Engine

Concurrency-safe, in-memory seat hold engine (Python 3.11+, standard library only).

## How to run tests

```
pip install pytest
pytest -q
```

Different seeds: `SEED=42 pytest -q` (model test) — also seeds the stress threads.
Stress length: `STRESS_OPS=2000 pytest -q tests/test_stress.py`.

## What works

- `hold` / `hold_many` (all-or-nothing, duplicate rejection, 4-seat limit on newly held seats only), `confirm` (late confirm succeeds only if seat still FREE, no TTL extension), `release`, `seat_status` (FREE / HELD / SOLD) — see DECISIONS.md D1–D6.
- Lazy expiry on every read/write; injectable clock (`FakeClock.advance`) — engine never calls `time.sleep` or the system clock.
- One global `threading.Lock` around every public method; typed errors for each failure mode.
- Differential test (10,000 seeded ops vs reference model) + 8-thread stress test (5 seats, fixed ops, invariant checks).

## What does not work

- Single-process only: the lock does not protect across processes or servers.
- No persistence: state is lost on restart; no reaping thread (expiry is lazy).
- No payment/waitlist integration; no per-seat TTL tuning beyond the `ttl_seconds` argument.
