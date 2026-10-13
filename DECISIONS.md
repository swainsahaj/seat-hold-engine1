# Decisions

## D1 (R3 vs R4): Confirming an expired hold succeeds only if the seat is still FREE at confirm time
Reasoning: Expiry returns the seat to FREE, so a late confirm is indistinguishable from a new claim by the holder unless the seat was taken by someone else in the meantime. Granting success only when the seat is still FREE preserves first-come-first-served fairness and avoids stealing a seat from a newer holder. The hold is never extended and gets no new TTL, so expiry stays a hard deadline and a stale hold_id cannot be reused to squat on the seat. Otherwise confirm returns an error.

## D2 (R5): hold() by the user who already holds the seat returns the existing hold_id
Reasoning: The operation is idempotent for the current holder: returning the same hold_id avoids leaking duplicate hold records and avoids double-counting toward limits. It does not consume a slot and does not reset the TTL, so a user cannot refresh an expiring hold by re-holding and cannot inflate their hold count by repeating the same seat.

## D3 (R5 vs R3): hold() by the same user on an expired hold is treated as a new claim if the seat is FREE
Reasoning: Once expired, the old hold no longer exists logically — the seat is FREE. Treating a re-hold as a fresh claim (new hold_id, fresh TTL) keeps expiry semantics uniform regardless of who held it before. Otherwise (seat taken by another active hold or SOLD) it returns an error, so neither the previous holder nor anyone else gets priority after expiry.

## D4 (R2 vs R6): hold_many counts only newly held seats toward the 4-seat limit
Reasoning: Idempotent re-holds (D2) do not increase the user's active-hold footprint, so they must not consume limit slots; only seats that transition FREE -> HELD for this user count. Duplicate seat IDs in one request are rejected, because silently de-duplicating would hide caller bugs and make the counted-vs-requested size ambiguous (atomic all-or-nothing semantics require a clear request size).

## D5 (counting): SOLD seats do not count toward the 4-seat limit
Reasoning: The limit applies to active holds (transient reservations that block others), not to completed purchases. A SOLD seat is terminal and no longer holds inventory hostage, so excluding it prevents punishing buyers and keeps the limit focused on contention.

## D6 (concurrency): One global lock around every public method
Reasoning: A single lock makes every public method atomic with trivial reasoning: no lock ordering, no partial acquisition, no deadlock from overlapping seat sets in hold_many. Throughput cost is acceptable for an in-memory engine with short critical sections. Alternative considered: per-seat locks with sorted-order acquisition, which allows more parallelism but was not chosen because it adds lock-ordering complexity, risks deadlock on any ordering bug, and complicates atomic multi-seat operations and limit checks spanning seats and per-user state.

## Ambiguities found in the spec

1. Late confirm on expired hold: (a) always fail vs (b) succeed if seat still FREE. Chose (b) per D1.
2. Re-hold by same holder: (a) new hold_id + TTL reset vs (b) idempotent return of existing hold_id with no TTL change. Chose (b) per D2.
3. Same-user re-hold after expiry: (a) revive old hold vs (b) new claim if FREE else error. Chose (b) per D3.
4. hold_many limit accounting: (a) count all requested seats vs (b) count only newly held seats. Chose (b) per D4.
5. Duplicate seat IDs within one hold_many: (a) de-duplicate silently vs (b) reject request. Chose (b) per D4.
6. Whether SOLD seats count toward the hold limit: (a) count vs (b) exclude. Chose (b) per D5.
7. Locking granularity: (a) global lock vs (b) per-seat locks with sorted-order acquisition. Chose (a) per D6.
8. Whether confirm extends an expired hold or grants a new TTL: (a) extend/renew vs (b) never extend, no new TTL. Chose (b) per D1.
