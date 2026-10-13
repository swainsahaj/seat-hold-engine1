# Bugs I Fear

## Multi-process / multi-server double sale (in-process lock only)

The engine's single `threading.Lock` serializes threads inside one process, but two processes or two servers each hold their own lock and their own in-memory maps. Both could confirm different holds for the same seat and each record a local SOLD — a double sale no in-process test can catch, because the test suite only exercises one address space.

## How to catch it in production

- Make the sale durable and exclusive: a database UNIQUE constraint on sold seats (one row per seat; the second `INSERT` fails atomically) with the confirm inside a transaction.
- Reconcile against payments: periodic job joining sold seats to payment records; alert on seats sold twice or sold without payment, and hold the seat unsellable until reconciled.
