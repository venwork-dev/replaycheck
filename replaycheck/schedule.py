"""Builds the deliveries to try.

A schedule holds the *logical* events -- what a correct run processes once -- plus
how delivery goes wrong. The delivered sequence is derived from that, so every
schedule can be compared against a clean run of its own events. That matters once
shrinking starts removing events.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


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
    events = list(events)
    families: dict[str, list] = {
        "crash": [
            Schedule(events=list(events), crash_after=n)
            for n in range(1, durable_writes + 1)
        ],
        "duplicate": [
            Schedule(events=list(events), duplicate_index=index)
            for index in range(len(events))
        ],
        "reorder": [],
    }
    if reorder:
        # Moving event i one place later and event i+1 one place earlier produce
        # the same delivery, so dedupe on the resulting order rather than on the
        # (source, target) pair. Without this every adjacent swap is enumerated
        # twice, which doubles the work and inflates this family's share of a
        # stratified sample.
        seen_orders = set()
        for source in range(len(events)):
            low = max(0, source - reorder)
            high = min(len(events) - 1, source + reorder)
            for target in range(low, high + 1):
                if target == source:
                    continue
                order = list(range(len(events)))
                order.insert(target, order.pop(source))
                fingerprint = tuple(order)
                if fingerprint in seen_orders:
                    continue
                seen_orders.add(fingerprint)
                families["reorder"].append(
                    Schedule(events=list(events), reorder=(source, target))
                )

    available = sum(len(group) for group in families.values())
    enabled = [name for name, group in families.items() if group]
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
        flat = [s for group in families.values() for s in group]
        return Plan(schedules=flat, available=available, sampled=False, seed=seed)

    quota = {}
    for name, group in families.items():
        if group:
            quota[name] = max(1, round(max_schedules * len(group) / available))
    while sum(quota.values()) > max_schedules:
        quota[max(quota, key=quota.get)] -= 1

    rng = random.Random(seed)
    picked = []
    for name, group in families.items():
        take = min(quota.get(name, 0), len(group))
        if take:
            picked += rng.sample(group, take)

    return Plan(schedules=picked, available=available, sampled=True, seed=seed)
