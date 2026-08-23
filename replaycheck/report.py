"""Turns a failing schedule into something a developer can act on."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .schedule import Schedule


def render_effect(entry) -> str:
    name, data = entry
    args = ", ".join(f"{k}={v}" for k, v in data)
    return f"{name}({args})"


@dataclass
class Failure:
    kind: str  # diverged | invariant | stalled
    message: str
    schedule: Schedule
    crash_effect: str | None = None
    crash_event_index: int | None = None
    baseline: list = field(default_factory=list)
    observed: list = field(default_factory=list)

    def headline(self) -> str:
        if self.kind == "raised":
            return "handler raised on an event it never commits"
        if self.schedule.crash_after is not None and self.crash_effect is not None:
            return f"crashed after {self.crash_effect}() on event {self.crash_event_index}"
        return self.schedule.describe()

    def detail_lines(self) -> list[str]:
        if self.kind == "diverged":
            baseline = Counter(self.baseline)
            observed = Counter(self.observed)
            lines = []
            for entry, n in sorted((observed - baseline).items()):
                lines.append(f"wrote {n}x extra: {render_effect(entry)}")
            for entry, n in sorted((baseline - observed).items()):
                lines.append(f"never wrote {n}x: {render_effect(entry)}")
            return lines or ["final state differs from the clean run"]
        return [self.message]


@dataclass
class Report:
    ok: bool
    schedules_run: int
    durable_writes: int
    failure: Failure | None = None
    schedules_available: int = 0
    sampled: bool = False
    seed: int = 0

    def __bool__(self) -> bool:
        return self.ok

    @property
    def complete(self) -> bool:
        """Did every available schedule actually run?"""
        return not self.sampled

    def text(self) -> str:
        if self.ok and self.sampled:
            return (
                f"PARTIAL  no divergence in {self.schedules_run} of "
                f"{self.schedules_available} schedules (sampled, seed {self.seed}); "
                f"raise max_schedules to cover the rest"
            )
        if self.ok:
            return (
                f"PASS  {self.schedules_run} schedules, "
                f"{self.durable_writes} durable writes, no divergence"
            )
        f = self.failure
        lines = [f"FAIL  {f.headline()}"]
        for line in f.detail_lines():
            lines.append(f"      {line}")
        events = f.schedule.events
        lines.append(f"      shortest failing input ({len(events)} event(s)):")
        for event in events[:3]:
            lines.append(f"        {event!r}")
        if len(events) > 3:
            lines.append(f"        ... and {len(events) - 3} more")
        return "\n".join(lines)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.text()
