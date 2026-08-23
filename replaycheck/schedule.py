"""Builds the deliveries to try.

A schedule holds the *logical* events -- what a correct run processes once -- plus
how delivery goes wrong. The delivered sequence is derived from that, so every
schedule can be compared against a clean run of its own events. That matters once
shrinking starts removing events.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def clean(events) -> Schedule:
    return Schedule(events=list(events))


def generate(
    events,
    durable_writes: int,
    max_schedules: int = 200,
    reorder: int = 0,
) -> list[Schedule]:
    events = list(events)
    schedules = [
        Schedule(events=list(events), crash_after=n)
        for n in range(1, durable_writes + 1)
    ]
    schedules += [
        Schedule(events=list(events), duplicate_index=index)
        for index in range(len(events))
    ]
    if reorder:
        for source in range(len(events)):
            low = max(0, source - reorder)
            high = min(len(events) - 1, source + reorder)
            for target in range(low, high + 1):
                if target != source:
                    schedules.append(
                        Schedule(events=list(events), reorder=(source, target))
                    )
    return schedules[:max_schedules]
