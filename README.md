# replaycheck

Find the bugs that only appear when an event is delivered twice.

## The bug it finds

Your handler charges a card, then marks the order paid:

```python
world.effect("charge", order=order, amount=event["amount"])
world.effect("paid", key=order, order=order)
```

Crash between those two lines and the retry charges the card again. Every backend
developer knows this bug exists. Almost nobody tests for it, because testing it
means simulating a crash at every single step.

## What it does

`replaycheck` runs your handler once cleanly to learn what the correct end state
looks like. Then it runs the same events again and again — each time killing the
process the instant a different side effect becomes durable, and resuming from
the last committed position, exactly as an at-least-once broker would. It also
delivers each event twice with no crash at all.

Every one of those runs has to end in the same durable state as the clean run.
When one doesn't, the failing schedule is shrunk to the shortest input that still
breaks.

## Try it

```
make demo
```

```
--- before ---
FAIL  crashed after charge() on event 0
      order-772 charged twice
      shortest failing input (1 event(s)):
        {'event_id': 'e2', 'order_id': 'order-772', 'amount': 1500}

--- after ---
PASS  7 schedules, 4 durable writes, no divergence
```

The only difference between the two handlers is one argument — `key=order` on
the charge sink, which makes it idempotent.

## Using it

Three things: your handler, some events, and one rule that must always hold.

```python
from replaycheck import check

def process_order(event, world):
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", key=order, order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)

def charged_at_most_once(world):
    for _, data in world.effects("charge"):
        assert world.count("charge", order=data["order"]) <= 1

report = check(process_order, events, invariant=charged_at_most_once)
assert report, report.text()
```

The handler writes through a `World` instead of a real database:

- `world.effect(name, key=None, **data)` — a durable write. Pass `key` and the
  sink deduplicates, the way a real idempotent endpoint would.
- `world.has(name, key)` — has this already been written?
- `world.count(name, **match)` / `world.effects(name)` — for invariants.

The harness owns the commit position: an event is committed when your handler
returns for it, and a crash resumes from the last one that did.

## Out-of-order arrival

Off by default, because a reordering is not automatically a bug — if your source
guarantees order, a divergence here is a property your handler was never required
to have. Turn it on when the source really can deliver out of order, and say how
far an event may slip:

```python
check(fulfil, events, reorder=1, compare=["paid", "shipped"])
```

```
FAIL  event 0 arrives 1 position(s) late
      never wrote 1x: shipped(order='order-771')
```

That handler drops a shipment that overtakes its payment. One that parks the
early shipment and releases it when the payment lands passes.

`compare` matters here. Reordering can legitimately leave different internal
bookkeeping behind while reaching the same business outcome — the tolerant
handler writes a `pending_ship` marker the canonical order never writes. Name the
effects that constitute the outcome and the rest is ignored. Without it you get a
failure for a handler that is doing the right thing.

```
make ordering
```

## Starting from state you already have

A consumer usually restarts against a database that is already partly populated,
and some replay bugs only appear from there. `setup` writes that state into every
run — baseline and schedules alike — before delivery starts. Writes made during
setup are pre-existing rows, never crash points.

```python
def already_paid(world):
    world.effect("paid", key="order-771", order="order-771")

check(process_order, events, setup=already_paid)
```

## When the handler raises

If your handler raises anything that is not a simulated crash, the event is never
committed, so an at-least-once broker redelivers it forever. That is a poison
pill, and it is reported as a failure naming the event rather than escaping as a
traceback:

```
FAIL  handler raised on an event it never commits
      handler raised KeyError on event 1: 'order_id' -- this event is never
      committed, so it is redelivered forever
```

If raising is what you intend, catch it in the handler and write a dead-letter
effect instead.

## Fixture hazards

Replay bugs hide behind well-behaved sample data. This says so before you trust
a fixture:

```
make hazards
```

```
ABSENT   duplicate delivery: 0 repeated event_id value(s) across 4 events
ABSENT   out-of-order arrival: 0 event(s) arrive with a timestamp before their predecessor
ABSENT   sequence gap: 0 gap(s) in offset

4 events, and none of them exercise: duplicate delivery, out-of-order arrival, sequence gap
```

## What it does not do

**The clean run is the oracle.** This checks that every failure schedule ends
where a clean run ends — it does not check that the clean run is *correct*. A
handler that charges the wrong amount every time is perfectly replay-invariant
and will pass:

```python
check(charges_999_always, events)                       # PASS
check(charges_999_always, events, invariant=amount_matches)   # FAIL
```

Divergence catches bugs that appear under replay. Everything else has to come
from the invariant you write. Nothing here measures conformance to a spec.

- It does not run your real database, broker, or network. You model side effects
  through `World`; the fidelity of the result is the fidelity of that model.
- It injects one crash per run. Interleaved failures across concurrent consumers
  are out of scope — that is Jepsen's problem, not this one.
- Reordering is bounded and opt-in: one event moved up to `reorder` positions.
  Arbitrary permutations and concurrent interleavings are not covered.
- A passing report means every schedule it tried ended in the same state. It is
  evidence, not a proof.

## Tests

```
make test
```
