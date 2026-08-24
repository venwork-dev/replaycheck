"""A table of known replay-bug patterns, each paired with its fix.

This is deliberate documentation as much as it is coverage: every row names a
bug class replaycheck claims to catch, points at the buggy handler that has it
and the fixed handler that doesn't, and pins the failure shape so a future
change to the checker can't silently stop catching one of these without a test
going red. New bug patterns should be added here, not folded into the general
test file, so the catalog stays a readable list rather than incidental
coverage.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replaycheck import check  # noqa: E402

# --- handlers, one buggy/fixed pair per bug pattern -------------------------

PAY_EVENTS = [
    {"event_id": "e1", "order_id": "order-771", "amount": 4200},
    {"event_id": "e2", "order_id": "order-772", "amount": 1500},
]


def charged_at_most_once(world):
    for _, data in world.effects("charge"):
        order = data["order"]
        assert world.count("charge", order=order) <= 1, f"{order} charged twice"


def double_charges_on_retry(event, world):
    """Crash between the two writes and a retry charges the card again."""
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def charges_at_most_once(event, world):
    """The only change: the charge sink is keyed too."""
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", key=order, order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def loses_the_charge_on_retry(event, world):
    """Marks paid before charging, so a crash-then-resume skips the charge."""
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("paid", key=order, order=order)
    world.effect("charge", key=order, order=order, amount=event["amount"])


ORDER_EVENTS = [
    {"type": "paid", "order_id": "order-771"},
    {"type": "shipped", "order_id": "order-771"},
]


def drops_the_early_shipment(event, world):
    """A shipment that overtakes its payment is silently dropped."""
    order = event["order_id"]
    if event["type"] == "paid":
        world.effect("paid", key=order, order=order)
    elif world.has("paid", order):
        world.effect("shipped", key=order, order=order)


def holds_the_early_shipment(event, world):
    """Parks an early shipment and releases it once the payment lands."""
    order = event["order_id"]
    if event["type"] == "paid":
        world.effect("paid", key=order, order=order)
        if world.has("pending_ship", order):
            world.effect("shipped", key=order, order=order)
    elif world.has("paid", order):
        world.effect("shipped", key=order, order=order)
    else:
        world.effect("pending_ship", key=order, order=order)


POISON_EVENTS = [{"order_id": "a"}, {"oops": "no order_id here"}]


def crashes_on_a_malformed_event(event, world):
    """A KeyError here is never committed, so the broker redelivers it forever."""
    order = event["order_id"]
    world.effect("charge", key=order, order=order)


def dead_letters_a_malformed_event(event, world):
    """Catches the bad event instead of letting it escape as a poison pill."""
    order = event.get("order_id")
    if order is None:
        world.effect("dlq", key=repr(event), data={"event": event})
        return
    world.effect("charge", key=order, order=order)


# --- the table itself --------------------------------------------------------


@dataclass
class BugPattern:
    name: str
    events: list
    buggy: callable
    fixed: callable
    expected_kind: str
    detail_substring: str | None = None
    invariant: callable | None = None
    kwargs: dict = field(default_factory=dict)


CATALOG = [
    BugPattern(
        name="double charge on crash-retry",
        events=PAY_EVENTS,
        buggy=double_charges_on_retry,
        fixed=charges_at_most_once,
        invariant=charged_at_most_once,
        expected_kind="invariant",
    ),
    BugPattern(
        name="charge lost when effects are written out of order",
        events=PAY_EVENTS,
        buggy=loses_the_charge_on_retry,
        fixed=charges_at_most_once,
        expected_kind="diverged",
        detail_substring="never wrote",
    ),
    BugPattern(
        name="dropped out-of-order event",
        events=ORDER_EVENTS,
        buggy=drops_the_early_shipment,
        fixed=holds_the_early_shipment,
        expected_kind="diverged",
        detail_substring="never wrote",
        kwargs={"reorder": 1, "compare": ["paid", "shipped"]},
    ),
    BugPattern(
        name="poison pill from an unhandled exception",
        events=POISON_EVENTS,
        buggy=crashes_on_a_malformed_event,
        fixed=dead_letters_a_malformed_event,
        expected_kind="raised",
    ),
]


@pytest.mark.parametrize("case", CATALOG, ids=[c.name for c in CATALOG])
def test_buggy_handler_is_caught(case: BugPattern):
    report = check(case.buggy, case.events, invariant=case.invariant, **case.kwargs)
    assert not report, f"{case.name}: expected a failure, got a PASS"
    assert report.failure.kind == case.expected_kind, (
        f"{case.name}: expected kind={case.expected_kind!r}, "
        f"got {report.failure.kind!r} -- {report.text()}"
    )
    if case.detail_substring:
        assert any(
            case.detail_substring in line for line in report.failure.detail_lines()
        ), f"{case.name}: {case.detail_substring!r} not in {report.failure.detail_lines()}"


@pytest.mark.parametrize("case", CATALOG, ids=[c.name for c in CATALOG])
def test_fixed_handler_passes(case: BugPattern):
    report = check(case.fixed, case.events, invariant=case.invariant, **case.kwargs)
    assert report, f"{case.name}: the fix should pass but got: {report.text()}"


# --- patterns that don't fit the buggy-fails/fixed-passes shape -------------


def test_replay_invariant_does_not_imply_correct():
    """The clean run is the oracle: wrong-but-consistent output passes without
    an invariant, and only fails once one is supplied that checks correctness."""

    def always_charges_999(event, world):
        world.effect("charge", key=event["order_id"], order=event["order_id"], amount=999)

    events = [{"order_id": "a", "amount": 4200}]

    assert check(always_charges_999, events), "replay-invariant, should pass with no invariant"

    def amount_matches(world):
        for _, data in world.effects("charge"):
            assert data["amount"] == 4200, "charged the wrong amount"

    report = check(always_charges_999, events, invariant=amount_matches)
    assert not report
    assert report.failure.kind == "invariant"
    assert "clean run" in report.failure.message


def test_blind_spot_note_only_fires_without_a_distinguishing_field():
    """Two orders for the same amount: correct until you can't tell whose
    charge is whose. Recording the order id closes the blind spot."""

    same_amount = [
        {"order_id": "o1", "amount": 50},
        {"order_id": "o2", "amount": 50},
    ]

    def charge_without_identity(event, world):
        world.effect("charge", key=event["order_id"], amount=event["amount"])

    def charge_with_identity(event, world):
        world.effect(
            "charge", key=event["order_id"], order=event["order_id"], amount=event["amount"]
        )

    report = check(charge_without_identity, same_amount)
    assert report, "both sinks are keyed, so there is no replay bug here"
    assert report.blind_spots, "identical writes for different orders should be flagged"
    assert "misattribution" in report.text()

    report = check(charge_with_identity, same_amount)
    assert report
    assert not report.blind_spots
    assert "NOTE" not in report.text()
