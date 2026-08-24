"""Builds the deliveries to try.

A schedule holds the *logical* events -- what a correct run processes once -- plus
how delivery goes wrong. The delivered sequence is derived from that, so every
schedule can be compared against a clean run of its own events. That matters once
shrinking starts removing events.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class Schedule:
    events: list
    crash_after: int | None = None
    duplicate_index: int | None = None
    reorder: tuple | None = None  # (from_index, to_index)

    @property
    def kind(self) -> str:
        if self.crash_after is not None:
            return "crash"
        if self.duplicate_index is not None:
            return "duplicate"
        if self.reorder is not None:
            return "reorder"
        return "clean"

    def delivered(self) -> list:
        """The sequence the broker actually hands over."""
        sequence = list(self.events)
        if self.reorder is not None:
            source, target = self.reorder
            sequence.insert(target, sequence.pop(source))
        if self.duplicate_index is not None:
            index = self.duplicate_index
            sequence = sequence[: index + 1] + [sequence[index]] + sequence[index + 1 :]
        return sequence

    def describe(self) -> str:
        if self.crash_after is not None:
            return f"crash the instant durable write #{self.crash_after} lands"
        if self.duplicate_index is not None:
            return f"event {self.duplicate_index} delivered twice"
        if self.reorder is not None:
            source, target = self.reorder
            when = "late" if target > source else "early"
            return f"event {source} arrives {abs(target - source)} position(s) {when}"
        return "clean run"


@dataclass
class Plan:
    """The schedules that will run, and how many there were to choose from."""

    schedules: list = field(default_factory=list)
    available: int = 0
    sampled: bool = False
    seed: int = 0

    def __len__(self) -> int:
        return len(self.schedules)

    def __iter__(self):
        return iter(self.schedules)


def clean(events) -> Schedule:
    return Schedule(events=list(events))


def generate(
    events,
    durable_writes: int,
    max_schedules: int = 200,
    reorder: int = 0,
    seed: int = 0,
) -> Plan:
    """Build the schedules to run.

    Over budget, this samples proportionally from each family rather than taking
    the first N. Taking the first N keeps only crash schedules and silently drops
    every duplicate and reorder, which turns a partial run into a confident-looking
    pass. ``max_schedules`` must provide at least one slot for every non-empty
    family; the clean baseline run does not count against this budget.
    """
    if durable_writes < 0:
        raise ValueError("durable_writes cannot be negative")
    if reorder < 0:
        raise ValueError("reorder cannot be negative")

    # Every schedule treats events as immutable. Sharing this one defensive copy
    # is what keeps a capped run bounded: previously every candidate copied the
    # whole stream before sampling, making plan construction O(events*schedules)
    # memory even when max_schedules was small.
    events = list(events)
    reorder_count = sum(1 for _ in _reorder_pairs(len(events), reorder))
    family_sizes = {
        "crash": durable_writes,
        "duplicate": len(events),
        "reorder": reorder_count,
    }

    available = sum(family_sizes.values())
    enabled = [name for name, size in family_sizes.items() if size]
    minimum = len(enabled)
    if max_schedules < minimum:
        if enabled:
            names = ", ".join(enabled)
            raise ValueError(
                f"max_schedules={max_schedules} is too small to sample every "
                f"enabled schedule family ({names}); set max_schedules to at "
                f"least {minimum}"
            )
        raise ValueError(
            f"max_schedules={max_schedules} cannot be negative; "
            "set max_schedules to at least 0"
        )
    if available <= max_schedules:
        flat = _pick_schedules(events, family_sizes, family_sizes, reorder, random.Random(seed))
        return Plan(schedules=flat, available=available, sampled=False, seed=seed)

    quota = {
        name: max(1, int(max_schedules * size / available))
        for name, size in family_sizes.items()
        if size
    }
    while sum(quota.values()) > max_schedules:
        name = max((name for name in quota if quota[name] > 1), key=quota.get)
        quota[name] -= 1
    while sum(quota.values()) < max_schedules:
        name = max(quota, key=lambda candidate: family_sizes[candidate] - quota[candidate])
        quota[name] += 1

    rng = random.Random(seed)
    picked = _pick_schedules(events, family_sizes, quota, reorder, rng)

    return Plan(schedules=picked, available=available, sampled=True, seed=seed)


def _reorder_pairs(event_count: int, distance: int) -> Iterator[tuple[int, int]]:
    """Yield each distinct bounded move without building delivery fingerprints.

    Moving i one position right is identical to moving i+1 one position left.
    Those adjacent left moves are the only duplicate representation, so skipping
    them avoids the former set of O(event_count)-sized fingerprints.
    """
    if not distance:
        return
    for source in range(event_count):
        low = max(0, source - distance)
        high = min(event_count - 1, source + distance)
        for target in range(low, high + 1):
            if target == source or target == source - 1:
                continue
            yield source, target


def _pick_schedules(events, sizes, quota, reorder, rng) -> list[Schedule]:
    """Instantiate only the descriptors selected by the schedule budget."""
    picked: list[Schedule] = []
    for index in sorted(rng.sample(range(sizes["crash"]), quota.get("crash", 0))):
        picked.append(Schedule(events=events, crash_after=index + 1))
    for index in sorted(rng.sample(range(sizes["duplicate"]), quota.get("duplicate", 0))):
        picked.append(Schedule(events=events, duplicate_index=index))

    reorder_indexes = set(
        rng.sample(range(sizes["reorder"]), quota.get("reorder", 0))
    )
    if reorder_indexes:
        for index, pair in enumerate(_reorder_pairs(len(events), reorder)):
            if index in reorder_indexes:
                picked.append(Schedule(events=events, reorder=pair))
    return picked
