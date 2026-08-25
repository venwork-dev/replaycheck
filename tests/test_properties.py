"""Generative and adversarial checks for the replay state space."""

from __future__ import annotations

from replaycheck import check
from replaycheck.schedule import generate


def test_every_bounded_reorder_descriptor_has_a_unique_delivery():
    """The compact pair representation must not create duplicate schedules."""
    for event_count in range(0, 13):
        events = [{"id": index} for index in range(event_count)]
        for distance in range(0, event_count + 2):
            plan = generate(
                events,
                durable_writes=0,
                max_schedules=10**6,
                reorder=distance,
            )
            deliveries = [
                tuple(event["id"] for event in schedule.delivered())
                for schedule in plan
                if schedule.kind == "reorder"
            ]
            assert len(deliveries) == len(set(deliveries))


def test_seeded_sampling_is_reproducible_and_covers_each_family():
    events = [{"id": index} for index in range(25)]
    first = generate(events, durable_writes=50, max_schedules=17, reorder=2, seed=41)
    second = generate(events, durable_writes=50, max_schedules=17, reorder=2, seed=41)

    describe = lambda schedule: (
        schedule.kind,
        schedule.crash_after,
        schedule.duplicate_index,
        schedule.reorder,
    )
    assert [describe(schedule) for schedule in first] == [
        describe(schedule) for schedule in second
    ]
    assert {schedule.kind for schedule in first} == {"crash", "duplicate", "reorder"}


def test_each_delivery_attempt_gets_a_fresh_nested_event_snapshot():
    source = [{"id": "a", "metadata": {"seen": []}}]

    def mutating_handler(event, world):
        event["metadata"]["seen"].append("attempt")
        world.effect("seen", key=event["id"], count=len(event["metadata"]["seen"]))

    report = check(mutating_handler, source)

    assert report
    assert source == [{"id": "a", "metadata": {"seen": []}}]


def test_setup_writes_are_preexisting_and_not_crash_points():
    events = [{"id": "a"}]

    def setup(world):
        world.effect("existing", key="a", id="a")

    def handler(event, world):
        if not world.has("existing", event["id"]):
            raise AssertionError("setup state was not visible")
        world.effect("processed", key=event["id"], id=event["id"])

    report = check(handler, events, setup=setup)

    assert report
    assert report.durable_writes == 1
