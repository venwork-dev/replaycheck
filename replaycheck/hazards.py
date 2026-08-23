"""Reports what a test fixture never contains.

Replay bugs hide behind well-behaved sample data. A fixture with no duplicates,
no out-of-order timestamps and no sequence gaps will pass a handler that breaks
on all three. This says so before you trust it.
"""

from __future__ import annotations

from dataclasses import dataclass

ID_FIELDS = ("event_id", "message_id", "id", "key", "uuid")
TIME_FIELDS = ("timestamp", "event_time", "ts", "time", "created_at", "occurred_at")
SEQUENCE_FIELDS = ("offset", "sequence", "seq", "position", "index")


@dataclass
class Hazard:
    name: str
    present: bool
    detail: str

    def __str__(self) -> str:
        mark = "present" if self.present else "ABSENT "
        return f"{mark}  {self.name}: {self.detail}"


def _pick(events, candidates) -> str | None:
    keys = {k for event in events if isinstance(event, dict) for k in event}
    for candidate in candidates:
        if candidate in keys:
            return candidate
    return None


def hazards(events, id_field=None, time_field=None, sequence_field=None) -> list[Hazard]:
    events = [e for e in events if isinstance(e, dict)]
    found: list[Hazard] = []
    if not events:
        return [Hazard("fixture", False, "no events to inspect")]

    id_field = id_field or _pick(events, ID_FIELDS)
    time_field = time_field or _pick(events, TIME_FIELDS)
    sequence_field = sequence_field or _pick(events, SEQUENCE_FIELDS)

    if id_field:
        seen, repeats, missing = set(), 0, 0
        for event in events:
            if id_field not in event:
                missing += 1
                continue
            value = event[id_field]
            if value in seen:
                repeats += 1
            seen.add(value)
        detail = f"{repeats} repeated {id_field} value(s) across {len(events)} events"
        if missing:
            detail += f"; {missing} event(s) have no {id_field} and were not checked"
        found.append(Hazard("duplicate delivery", repeats > 0, detail))
    else:
        found.append(Hazard("duplicate delivery", False, "no id-like field to check"))

    if time_field:
        regressions = incomparable = 0
        for first, second in zip(events, events[1:]):
            if time_field not in first or time_field not in second:
                incomparable += 1
                continue
            outcome = _lt(second[time_field], first[time_field])
            if outcome is None:
                incomparable += 1
            elif outcome:
                regressions += 1
        detail = (
            f"{regressions} event(s) arrive with a {time_field} before their predecessor"
        )
        if incomparable:
            detail += f"; {incomparable} pair(s) could not be compared"
        found.append(Hazard("out-of-order arrival", regressions > 0, detail))
    else:
        found.append(Hazard("out-of-order arrival", False, "no time-like field to check"))

    if sequence_field:
        gaps = 0
        for a, b in zip(events, events[1:]):
            first, second = a.get(sequence_field), b.get(sequence_field)
            if isinstance(first, int) and isinstance(second, int) and second - first > 1:
                gaps += 1
        found.append(
            Hazard(
                "sequence gap",
                gaps > 0,
                f"{gaps} gap(s) in {sequence_field}",
            )
        )
    else:
        found.append(Hazard("sequence gap", False, "no sequence-like field to check"))

    return found


def _lt(left, right):
    """True, False, or None when the two values are not comparable."""
    try:
        return left < right
    except TypeError:
        return None
