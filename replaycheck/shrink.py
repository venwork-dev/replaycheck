"""Trims a failing schedule down to the shortest one that still fails.

This is the difference between "something broke in one of 40 runs" and a bug
report a developer can read in one line.
"""

from __future__ import annotations

from .schedule import Schedule


def _without(schedule: Schedule, index: int) -> Schedule | None:
    events = schedule.events[:index] + schedule.events[index + 1 :]
    if not events:
        return None

    duplicate = schedule.duplicate_index
    if duplicate is not None:
        if duplicate == index:
            duplicate = None
        elif duplicate > index:
            duplicate -= 1

    reorder = schedule.reorder
    if reorder is not None:
        source, target = reorder
        if index in (source, target):
            reorder = None
        else:
            source -= 1 if source > index else 0
            target -= 1 if target > index else 0
            reorder = (source, target)

    return Schedule(
        events=events,
        crash_after=schedule.crash_after,
        duplicate_index=duplicate,
        reorder=reorder,
    )


def shrink(schedule: Schedule, still_fails) -> Schedule:
    """Greedily drop events, then pull the crash point as early as it will go."""
    best = schedule

    dropping = True
    while dropping:
        dropping = False
        for index in range(len(best.events)):
            candidate = _without(best, index)
            if candidate is not None and still_fails(candidate):
                best = candidate
                dropping = True
                break

    if best.crash_after is not None:
        for n in range(1, best.crash_after):
            candidate = Schedule(
                events=list(best.events),
                crash_after=n,
                duplicate_index=best.duplicate_index,
                reorder=best.reorder,
            )
            if still_fails(candidate):
                best = candidate
                break

    return best
