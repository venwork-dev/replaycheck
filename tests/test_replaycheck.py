import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replaycheck import World, check, hazards, run  # noqa: E402

EVENTS = [
    {"event_id": "e1", "order_id": "order-771", "amount": 4200},
    {"event_id": "e2", "order_id": "order-772", "amount": 1500},
]


def unkeyed_charge(event, world):
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def keyed_charge(event, world):
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", key=order, order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def read_only(event, world):
    world.has("paid", event["order_id"])


def charged_once(world):
    for _, data in world.effects("charge"):
        order = data["order"]
        assert world.count("charge", order=order) <= 1, f"{order} charged twice"
    return True


def test_replay_bug_is_caught():
    report = check(unkeyed_charge, EVENTS, invariant=charged_once)
    assert not report
    assert report.failure.kind in {"invariant", "diverged"}
    assert report.failure.schedule.crash_after is not None


def test_failing_schedule_is_shrunk_to_one_event():
    report = check(unkeyed_charge, EVENTS, invariant=charged_once)
    assert len(report.failure.schedule.events) == 1


def test_report_names_the_crashing_sink():
    report = check(unkeyed_charge, EVENTS, invariant=charged_once)
    assert "charge" in report.failure.headline()
    assert "charged twice" in report.text()


def test_keyed_sink_passes_every_schedule():
    report = check(keyed_charge, EVENTS, invariant=charged_once)
    assert report
    assert report.durable_writes == 4
    assert "PASS" in report.text()


def test_divergence_is_caught_without_an_invariant():
    report = check(unkeyed_charge, EVENTS)
    assert not report
    assert report.failure.kind == "diverged"
    assert any("extra" in line for line in report.failure.detail_lines())


def test_handler_with_no_writes_still_runs_duplicate_schedules():
    report = check(read_only, EVENTS)
    assert report
    assert report.durable_writes == 0
    assert report.schedules_run == len(EVENTS) + 1


def test_invariant_failing_on_the_clean_run_is_reported_as_such():
    def always_false(world):
        assert False, "nope"

    report = check(keyed_charge, EVENTS, invariant=always_false)
    assert not report
    assert "clean run" in report.failure.message


def test_crash_fires_once_so_replay_terminates():
    world = run(unkeyed_charge, EVENTS, crash_after=1)
    assert world.crashed
    assert world.count("charge", order="order-771") == 2


def test_idempotent_sink_suppresses_the_second_write():
    world = World()
    assert world.effect("charge", key="a", order="a") is True
    assert world.effect("charge", key="a", order="a") is False
    assert world.count("charge") == 1


def test_hazards_flags_a_fixture_with_nothing_interesting_in_it():
    findings = {f.name: f for f in hazards(EVENTS)}
    assert findings["duplicate delivery"].present is False
    assert findings["out-of-order arrival"].present is False


def test_hazards_sees_real_hazards():
    dirty = [
        {"event_id": "e1", "timestamp": 10, "offset": 0},
        {"event_id": "e1", "timestamp": 5, "offset": 4},
    ]
    findings = {f.name: f for f in hazards(dirty)}
    assert findings["duplicate delivery"].present
    assert findings["out-of-order arrival"].present
    assert findings["sequence gap"].present


def test_cli_reports_absences(tmp_path):
    fixture = tmp_path / "events.jsonl"
    fixture.write_text("\n".join(json.dumps(e) for e in EVENTS))
    result = subprocess.run(
        [sys.executable, "-m", "replaycheck", "hazards", str(fixture)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0
    assert "ABSENT" in result.stdout
    assert "none of them exercise" in result.stdout


@pytest.mark.parametrize("crash_after", [1, 2, 3, 4])
def test_every_crash_point_terminates(crash_after):
    world = run(keyed_charge, EVENTS, crash_after=crash_after)
    assert world.count("paid") == 2


def paid_before_charge(event, world):
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("paid", key=order, order=order)
    world.effect("charge", key=order, order=order, amount=event["amount"])


def test_lost_effect_is_caught_not_just_duplicated_ones():
    report = check(paid_before_charge, EVENTS)
    assert not report
    assert report.failure.kind == "diverged"
    assert any("never wrote" in line for line in report.failure.detail_lines())


def test_poison_pill_is_reported_not_raised():
    def unguarded(event, world):
        world.effect("charge", key=event["order_id"], order=event["order_id"])

    report = check(unguarded, [{"order_id": "a"}, {"oops": "x"}])
    assert not report
    assert report.failure.kind == "raised"
    assert "redelivered forever" in report.failure.message
    assert "raised" in report.failure.headline()


def test_setup_seeds_pre_existing_state():
    def already_paid(world):
        world.effect("paid", key="order-771", order="order-771")

    report = check(unkeyed_charge, [{"order_id": "order-771", "amount": 1}], setup=already_paid)
    assert report, report.text()
    assert report.durable_writes == 0


def test_setup_effects_are_not_crash_points():
    def seeded(world):
        world.effect("seed", key="s", note="pre-existing")

    world = run(keyed_charge, EVENTS, crash_after=1, setup=seeded)
    assert world.crash_effect == "charge"
    assert world.count("seed") == 1


def test_replay_invariance_does_not_imply_correctness():
    """The clean run is the oracle, so a handler wrong on the happy path passes."""

    def wrong_amount(event, world):
        world.effect("charge", key=event["order_id"], order=event["order_id"], amount=999)

    assert check(wrong_amount, [{"order_id": "a", "amount": 4200}])

    def amount_matches(world):
        for _, data in world.effects("charge"):
            assert data["amount"] == 4200, "charged the wrong amount"

    assert not check(wrong_amount, [{"order_id": "a", "amount": 4200}], invariant=amount_matches)


ORDER_EVENTS = [
    {"type": "paid", "order_id": "order-771"},
    {"type": "shipped", "order_id": "order-771"},
]


def order_dependent(event, world):
    order = event["order_id"]
    if event["type"] == "paid":
        world.effect("paid", key=order, order=order)
    elif world.has("paid", order):
        world.effect("shipped", key=order, order=order)


def order_tolerant(event, world):
    order = event["order_id"]
    if event["type"] == "paid":
        world.effect("paid", key=order, order=order)
        if world.has("pending_ship", order):
            world.effect("shipped", key=order, order=order)
    elif world.has("paid", order):
        world.effect("shipped", key=order, order=order)
    else:
        world.effect("pending_ship", key=order, order=order)


def test_reordering_is_off_by_default():
    assert check(order_dependent, ORDER_EVENTS)


def test_reordering_catches_a_dropped_out_of_order_event():
    report = check(order_dependent, ORDER_EVENTS, reorder=1, compare=["paid", "shipped"])
    assert not report
    assert report.failure.schedule.kind == "reorder"
    assert "arrives" in report.failure.headline()
    assert any("never wrote" in line for line in report.failure.detail_lines())


def test_a_tolerant_handler_passes_reordering():
    assert check(order_tolerant, ORDER_EVENTS, reorder=1, compare=["paid", "shipped"])


def test_compare_scopes_the_state_comparison():
    """Without compare, internal bookkeeping counts as a divergence."""
    assert not check(order_tolerant, ORDER_EVENTS, reorder=1)
    assert check(order_tolerant, ORDER_EVENTS, reorder=1, compare=["paid", "shipped"])


def test_sampling_covers_every_family_not_just_the_first():
    """Head-truncation kept only crash schedules and still reported PASS."""
    from collections import Counter

    from replaycheck.schedule import generate

    events = [{"order_id": f"o{i}"} for i in range(400)]
    plan = generate(events, durable_writes=800, max_schedules=200, reorder=1)
    kinds = Counter(s.kind for s in plan)
    assert kinds["crash"] and kinds["duplicate"] and kinds["reorder"]
    assert plan.sampled
    assert plan.available > len(plan)


@pytest.mark.parametrize(
    ("events", "durable_writes", "reorder", "families"),
    [
        ([], 0, 0, set()),
        ([], 2, 0, {"crash"}),
        ([{"id": "a"}, {"id": "b"}], 0, 0, {"duplicate"}),
        ([{"id": "a"}, {"id": "b"}], 2, 0, {"crash", "duplicate"}),
        ([{"id": "a"}, {"id": "b"}], 0, 1, {"duplicate", "reorder"}),
        (
            [{"id": "a"}, {"id": "b"}],
            2,
            1,
            {"crash", "duplicate", "reorder"},
        ),
    ],
)
def test_schedule_budget_must_cover_each_enabled_family(
    events, durable_writes, reorder, families
):
    from replaycheck.schedule import generate

    minimum = len(families)
    with pytest.raises(ValueError) as exc_info:
        generate(
            events,
            durable_writes=durable_writes,
            max_schedules=minimum - 1,
            reorder=reorder,
        )

    message = str(exc_info.value)
    assert f"set max_schedules to at least {minimum}" in message
    assert all(family in message for family in families)

    plan = generate(
        events,
        durable_writes=durable_writes,
        max_schedules=minimum,
        reorder=reorder,
    )
    assert {schedule.kind for schedule in plan} == families


def test_a_sampled_run_does_not_claim_to_be_a_pass():
    events = [{"order_id": f"o{i}", "amount": i} for i in range(400)]
    report = check(keyed_charge, events, max_schedules=50)
    assert report
    assert not report.complete
    assert "PARTIAL" in report.text()
    assert "raise max_schedules" in report.text()


def test_a_complete_run_says_so():
    report = check(keyed_charge, EVENTS)
    assert report.complete
    assert "PASS" in report.text()


def test_sweep_finds_a_replay_bug_from_short_streams():
    from replaycheck import sweep

    def make_events(rng):
        return [
            {"order_id": f"o{rng.randint(0, 3)}", "amount": rng.randrange(1, 999)}
            for _ in range(rng.randint(1, 4))
        ]

    report = sweep(unkeyed_charge, make_events, runs=100)
    assert not report
    assert len(report.failure.schedule.events) <= 2


def test_sweep_passes_a_correct_handler():
    from replaycheck import sweep

    def make_events(rng):
        return [{"order_id": f"o{rng.randint(0, 3)}", "amount": 1} for _ in range(3)]

    assert sweep(keyed_charge, make_events, runs=50)


SAME_AMOUNT = [{"order_id": "o1", "amount": 50}, {"order_id": "o2", "amount": 50}]


def charge_without_identity(event, world):
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", key=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def charge_with_identity(event, world):
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", key=order, order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def test_identical_amounts_do_not_confuse_duplicate_detection():
    """Two orders for the same amount is not a duplicate; a third charge is."""

    def unkeyed(event, world):
        order = event["order_id"]
        if world.has("paid", order):
            return
        world.effect("charge", order=order, amount=event["amount"])
        world.effect("paid", key=order, order=order)

    report = check(unkeyed, SAME_AMOUNT)
    assert not report
    assert any("extra" in line for line in report.failure.detail_lines())


def test_indistinguishable_writes_are_flagged():
    report = check(charge_without_identity, SAME_AMOUNT)
    assert report, "no replay bug here -- both sinks are keyed"
    assert report.blind_spots, "identical writes should be reported"
    assert "misattribution" in report.text()


def test_recording_an_identity_clears_the_flag():
    report = check(charge_with_identity, SAME_AMOUNT)
    assert report
    assert not report.blind_spots
    assert "NOTE" not in report.text()


def test_a_misattribution_is_invisible_without_an_identity():
    """The limit the note warns about, pinned so it stays honest."""
    correct, swapped = World(), World()
    correct.effect("charge", amount=50)
    correct.effect("charge", amount=50)
    swapped.effect("charge", amount=50)
    swapped.effect("charge", amount=50)
    assert correct.fingerprint() == swapped.fingerprint()

    correct, swapped = World(), World()
    correct.effect("charge", order="o1", amount=50)
    correct.effect("charge", order="o2", amount=50)
    swapped.effect("charge", order="o1", amount=50)
    swapped.effect("charge", order="o1", amount=50)
    assert correct.fingerprint() != swapped.fingerprint()


# --- regressions from the code review ---------------------------------------


def test_compare_as_a_bare_string_is_rejected():
    """set('charge') is its characters, which would compare nothing and pass."""
    with pytest.raises(TypeError, match="not a string"):
        check(unkeyed_charge, EVENTS, compare="charge")


def test_compare_that_matches_no_effect_is_rejected():
    with pytest.raises(ValueError, match="matches none of the effects"):
        check(unkeyed_charge, EVENTS, compare=["chrage"])


def test_empty_compare_is_rejected():
    with pytest.raises(ValueError, match="compare is empty"):
        check(unkeyed_charge, EVENTS, compare=[])


def test_sweep_reports_partial_coverage_from_its_inner_runs():
    from replaycheck import sweep

    def three_writes(event, world):
        for name in ("a", "b", "c"):
            world.effect(name, key=event["id"])

    report = sweep(
        three_writes,
        lambda rng: [{"id": f"x{i}"} for i in range(3)],
        runs=2,
        max_schedules=3,
    )
    assert report
    assert report.sampled and not report.complete
    assert report.durable_writes > 0
    assert "PARTIAL" in report.text()


def test_a_stalling_clean_run_is_reported_not_raised():
    from replaycheck import Crash

    def never_finishes(event, world):
        raise Crash("boom")

    report = check(never_finishes, EVENTS)
    assert not report
    assert report.failure.kind == "stalled"
    assert "never finishes" in report.failure.headline()


def test_a_field_named_key_can_still_be_recorded():
    world = World()
    world.arm(None)
    world.effect("dlq", key="idem-1", data={"key": "kafka-a"})
    world.effect("dlq", key="idem-2", data={"key": "kafka-b"})
    assert world.count("dlq") == 2
    assert world.count("dlq", key="kafka-a") == 1


def test_hazards_does_not_count_missing_ids_as_duplicates():
    findings = {f.name: f for f in hazards([{"event_id": "a"}, {"other": 1}, {"other": 2}])}
    duplicate = findings["duplicate delivery"]
    assert duplicate.present is False
    assert "were not checked" in duplicate.detail


def test_hazards_says_when_timestamps_cannot_be_compared():
    findings = {f.name: f for f in hazards([{"timestamp": 1}, {"timestamp": "x"}])}
    assert "could not be compared" in findings["out-of-order arrival"].detail


def test_each_reorder_delivery_is_generated_once():
    from replaycheck.schedule import generate

    events = [{"i": i} for i in range(4)]
    plan = generate(events, durable_writes=0, max_schedules=10**9, reorder=1)
    orders = [
        tuple(e["i"] for e in s.delivered()) for s in plan if s.kind == "reorder"
    ]
    assert len(orders) == len(set(orders))


def test_max_schedules_below_the_family_count_is_rejected():
    """Without this the quota loop silently drops a whole family."""
    from replaycheck.schedule import generate

    events = [{"i": i} for i in range(5)]
    with pytest.raises(ValueError, match="too small to sample"):
        generate(events, durable_writes=10, max_schedules=2, reorder=1)
