"""The fake ledger a handler writes to.

A World records durable side effects. Two things make it useful for finding
replay bugs: effects can carry an idempotency key, and the harness can kill the
run immediately after any effect becomes durable -- which is exactly the window
where at-least-once delivery bites.
"""

from __future__ import annotations


class Crash(RuntimeError):
    """Raised inside a durable sink to simulate the process dying."""


class World:
    def __init__(self, crash_after: int | None = None):
        self._effects: list[tuple[str, dict]] = []
        self._keys: set[tuple[str, str]] = set()
        self._applied = 0
        self._crash_after = crash_after
        self.crashed = False
        self.crash_effect: str | None = None
        self.crash_event_index: int | None = None

    # -- what a handler calls ------------------------------------------------

    def effect(self, name: str, key: str | None = None, **data) -> bool:
        """Write a durable side effect. Returns False if the sink deduplicated it.

        Passing ``key`` makes the sink idempotent for that key: a second call
        with the same name and key writes nothing.
        """
        if key is not None and (name, key) in self._keys:
            return False
        if key is not None:
            self._keys.add((name, key))
        self._effects.append((name, dict(data)))
        self._applied += 1

        if self._crash_after == self._applied and not self.crashed:
            self.crashed = True
            self.crash_effect = name
            raise Crash(f"crashed after {name}()")
        return True

    def arm(self, crash_after: int | None) -> None:
        """Finish setup and start counting durable writes for crash injection.

        Effects written before this are pre-existing state -- the rows already in
        your database when the consumer restarts -- and are never crash points.
        """
        self._crash_after = crash_after
        self._applied = 0

    def has(self, name: str, key: str) -> bool:
        """Has an effect with this name and idempotency key already been written?"""
        return (name, key) in self._keys

    # -- what an invariant calls ---------------------------------------------

    def count(self, name: str, **match) -> int:
        return sum(
            1
            for effect_name, data in self._effects
            if effect_name == name and all(data.get(k) == v for k, v in match.items())
        )

    def effects(self, name: str | None = None) -> list[tuple[str, dict]]:
        if name is None:
            return list(self._effects)
        return [(n, d) for n, d in self._effects if n == name]

    # -- what the harness calls ----------------------------------------------

    @property
    def applied(self) -> int:
        return self._applied

    def fingerprint(self) -> list[tuple[str, tuple]]:
        """Order-insensitive summary of everything durable this run wrote."""
        return sorted(
            (name, tuple(sorted((k, repr(v)) for k, v in data.items())))
            for name, data in self._effects
        )

    def __repr__(self) -> str:
        return f"<World effects={len(self._effects)} crashed={self.crashed}>"
