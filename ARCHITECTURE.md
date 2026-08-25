# Architecture

`replaycheck` is a deterministic, in-process model checker for at-least-once
event handlers. It does not connect to a broker or database. An adapter calls a
`World` instead of real side-effecting clients, and the checker compares the
resulting durable ledger across delivery schedules.

## Execution flow

1. `check()` materializes the input iterable once because every schedule must
   replay the same events.
2. `run()` performs a clean delivery. The resulting `World` is the behavioral
   oracle and its applied writes identify the available crash points.
3. `generate()` counts crash, duplicate, and optional reorder candidates. If the
   count exceeds `max_schedules`, it samples each enabled family with a seeded
   random generator. Only selected schedule descriptors are created, and all of
   them share the checker's immutable event list.
4. `run()` executes each selected schedule. Every delivery receives an isolated
   copy of its event. A `Crash` raised immediately after a durable effect leaves
   that snapshotted effect in the `World`, while the current event is uncommitted
   and is delivered again.
5. `check()` compares the world's order-insensitive effect fingerprint with the
   clean run and evaluates the caller's invariant.
6. On failure, `shrink()` removes events and moves the crash point earlier while
   preserving the same failure class.

The main modules follow those boundaries:

- `checker.py`: orchestration, comparison, invariants, and randomized sweeps
- `runner.py`: at-least-once commit/retry behavior
- `world.py`: durable effects, idempotency keys, and crash injection
- `schedule.py`: bounded schedule enumeration and seeded sampling
- `shrink.py`: minimal failing cases
- `report.py`: stable human-readable results
- `__main__.py`: fixture inspection and external-repository adapter loading

## Scaling model

With `B = max_schedules`, `N = events`, and `W = durable writes`, a capped check
stores one event list, at most `B` schedule descriptors, and one world's effects
at a time. Execution still intentionally costs roughly `O(B * (N + W))`: each
selected schedule is a fresh replay. Fingerprint sorting adds
`O(W log W)` per schedule.

This makes a bounded run practical for transaction fixtures in the thousands or
tens of thousands. It does not make exhaustive enumeration of a large stream
practical. For broader state-space coverage, generate many short streams with
`sweep()`; for a representative medium fixture, set a deterministic seed and a
schedule budget that fits CI.

The complete input remains in memory because replay requires repeatable access.
JSON Lines input is decoded incrementally by the CLI to avoid a second full text
buffer, but the decoded events are retained. Truly disk-backed replay would need
a separate rewindable event-source abstraction.

## Benchmarking and CI budgets

Run the dependency-free scaling benchmark with:

```console
make benchmark
```

It reports wall-clock time, selected schedules, and available schedules for
100-, 400-, and 800-event streams. The benchmark is intentionally not part of
the default CI gate because host runners vary; use it to compare changes on the
same machine. For CI checks, prefer a few hundred short streams through
`sweep()` or a capped `check(..., max_schedules=...)` with a fixed `seed`.

Treat a sampled `PARTIAL` report as an explicit coverage decision. Use the CLI's
`--fail-on-partial` option when a pipeline must reject an incomplete schedule
budget, and record the selected budget and seed alongside the test result.

## Integration boundary

An application repository owns an adapter with the signature
`handler(event, world)`. The adapter should preserve the application's branching
and idempotency decisions while translating database writes, API calls, and
published messages into `world.effect(...)`. Invariants inspect that ledger.

Do not call a live database, payment processor, or broker from a replaycheck
adapter. Every schedule deliberately repeats work and injects failure after
writes. Integration tests against real infrastructure need a separate disposable
environment and are outside this model checker's current contract.
