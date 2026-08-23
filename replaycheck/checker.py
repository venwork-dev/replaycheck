"""The entry point: replay a handler under every failure schedule and compare.

Each schedule is judged against a clean run of its *own* logical events, so a
shrunk schedule is compared against the right baseline rather than the original
one.
"""

from __future__ import annotations

import random

from .report import Failure, Report
from .runner import HandlerError, Stalled, run
from .schedule import Schedule, clean, generate
from .shrink import shrink


def _evaluate_invariant(invariant, world) -> tuple[bool, str]:
    if invariant is None:
        return True, ""
    try:
        result = invariant(world)
    except AssertionError as exc:
        return False, str(exc) or "invariant assertion failed"
    if result is False:
        return False, "invariant returned False"
    return True, ""


def check(
    handler,
    events,
    invariant=None,
    setup=None,
    reorder: int = 0,
    compare=None,
    max_schedules: int = 200,
    seed: int = 0,
) -> Report:
    """Replay ``handler`` over ``events`` under crash and duplicate schedules.

    Every schedule must end in the same durable state as a clean run of the same
    events, and must satisfy ``invariant`` if one is given. Returns a Report,
    which is falsey when a schedule broke.

    ``setup`` writes pre-existing state into every world before delivery starts,
    for the common case where a consumer restarts against a database that is
    already partly populated.

    ``reorder`` is off by default. Set it to the number of positions an event may
    arrive out of place, and every such arrival order must reach the same final
    state as the canonical one. Only enable it when your source really can
    deliver out of order -- if ordering is guaranteed, a divergence here is a
    property your handler was never required to have.

    ``compare`` narrows the state comparison to the named effects. Reordering can
    legitimately leave different internal bookkeeping behind while reaching the
    same business outcome, so name the effects that constitute that outcome and
    the rest is ignored.
    """
    events = list(events)
    baselines: dict[str, list] = {}
    wanted = None if compare is None else set(compare)

    def state_of(world):
        fingerprint = world.fingerprint()
        if wanted is None:
            return fingerprint
        return [entry for entry in fingerprint if entry[0] in wanted]

    def baseline_for(logical_events):
        cache_key = repr(logical_events)
        if cache_key not in baselines:
            try:
                world = run(handler, logical_events, setup=setup)
            except (HandlerError, Stalled):
                baselines[cache_key] = None
            else:
                ok, _ = _evaluate_invariant(invariant, world)
                baselines[cache_key] = state_of(world) if ok else None
        return baselines[cache_key]

    try:
        root = run(handler, events, setup=setup)
    except HandlerError as exc:
        return Report(
            ok=False,
            schedules_run=1,
            durable_writes=0,
            failure=Failure(
                kind="raised",
                message=f"{exc} -- this event is never committed, so it is redelivered forever",
                schedule=Schedule(events=[exc.event]),
            ),
        )
    ok, message = _evaluate_invariant(invariant, root)
    if not ok:
        return Report(
            ok=False,
            schedules_run=1,
            durable_writes=root.applied,
            failure=Failure(
                kind="invariant",
                message=f"invariant already fails on the clean run: {message}",
                schedule=clean(events),
                baseline=state_of(root),
                observed=state_of(root),
            ),
        )

    def inspect(schedule: Schedule) -> Failure | None:
        baseline = baseline_for(schedule.events)
        if baseline is None:
            # The clean run of these events is already broken, so this schedule
            # proves nothing about replay.
            return None

        try:
            world = run(
                handler,
                schedule.delivered(),
                crash_after=schedule.crash_after,
                setup=setup,
            )
        except HandlerError as exc:
            return Failure(
                kind="raised",
                message=f"{exc} -- this event is never committed, so it is redelivered forever",
                schedule=schedule,
                baseline=baseline,
            )
        except Stalled as exc:
            return Failure(
                kind="stalled",
                message=str(exc),
                schedule=schedule,
                baseline=baseline,
            )

        observed = state_of(world)
        ok, message = _evaluate_invariant(invariant, world)
        common = dict(
            schedule=schedule,
            crash_effect=world.crash_effect,
            crash_event_index=world.crash_event_index,
            baseline=baseline,
            observed=observed,
        )
        if not ok:
            return Failure(kind="invariant", message=message, **common)
        if observed != baseline:
            return Failure(
                kind="diverged",
                message="final state differs from the clean run",
                **common,
            )
        return None

    plan = generate(
        events,
        root.applied,
        max_schedules=max_schedules,
        reorder=reorder,
        seed=seed,
    )

    for position, schedule in enumerate(plan, start=2):
        failure = inspect(schedule)
        if failure is None:
            continue

        def still_fails(candidate: Schedule, kind=failure.kind) -> bool:
            found = inspect(candidate)
            return found is not None and found.kind == kind

        smallest = shrink(schedule, still_fails)
        return Report(
            ok=False,
            schedules_run=position,
            durable_writes=root.applied,
            failure=inspect(smallest) or failure,
            schedules_available=plan.available,
            sampled=plan.sampled,
            seed=plan.seed,
        )

    return Report(
        ok=True,
        schedules_run=len(plan) + 1,
        durable_writes=root.applied,
        schedules_available=plan.available,
        sampled=plan.sampled,
        seed=plan.seed,
    )


def sweep(handler, make_events, runs: int = 200, seed: int = 0, **kwargs) -> Report:
    """Check many small generated streams instead of one large one.

    Replay bugs are local: every failure this tool finds shrinks to one or two
    events. Enumerating schedules for one long stream costs O(n^2) and mostly
    re-tests the same handful of transitions, so a few hundred short random
    streams find the same bugs for a fraction of the work.

    ``make_events(rng)`` returns one stream. The first failing report is returned
    as-is, with its shrunk schedule naming the events that broke it.
    """
    rng = random.Random(seed)
    checked = 0
    for _ in range(runs):
        report = check(handler, make_events(rng), **kwargs)
        checked += report.schedules_run
        if not report:
            return report
    return Report(ok=True, schedules_run=checked, durable_writes=0)
