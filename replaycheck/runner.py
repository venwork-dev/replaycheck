"""Replays a handler over events under at-least-once delivery.

An event is committed only when the handler returns for it. If the handler dies
partway, the run resumes from the last committed position -- so every event
after that point is delivered again, side effects included.
"""

from __future__ import annotations

from copy import deepcopy

from .world import Crash, World


class Stalled(RuntimeError):
    """The run never got past an event, so no final state exists to compare."""


class HandlerError(RuntimeError):
    """The handler raised something that is not a Crash.

    Under at-least-once delivery that event is never committed, so the broker
    redelivers it forever -- a poison pill. Handle it in the handler (route it to
    a dead-letter effect) rather than letting it escape.
    """

    def __init__(self, event_index: int, event, original: BaseException):
        super().__init__(
            f"handler raised {type(original).__name__} on event {event_index}: {original}"
        )
        self.event_index = event_index
        self.event = event
        self.original = original


def run(
    handler,
    events,
    crash_after: int | None = None,
    max_attempts: int | None = None,
    setup=None,
) -> World:
    world = World()
    if setup is not None:
        setup(world)
    world.arm(crash_after)
    events = list(events)
    committed = 0
    attempts = 0
    cap = max_attempts if max_attempts is not None else len(events) + 5

    while committed < len(events):
        attempts += 1
        if attempts > cap:
            raise Stalled(
                f"handler did not get past event {committed} after {attempts - 1} attempts"
            )
        index = committed
        try:
            for index in range(committed, len(events)):
                # A broker delivery is a value, not shared mutable test state.
                # Isolating each attempt prevents a handler that normalizes a
                # dict in place from changing retries or later schedules.
                handler(deepcopy(events[index]), world)
                committed = index + 1
        except Crash:
            world.crash_event_index = index
            continue
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            raise HandlerError(index, events[index], exc) from exc

    return world
