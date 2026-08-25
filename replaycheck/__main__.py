"""Command-line interface for fixture inspection and external-repository checks."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from .checker import check
from .hazards import hazards
from . import __version__


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _load(path: str) -> list:
    """Load a JSON array or JSON Lines file without buffering JSONL as text."""
    source = Path(path)
    with source.open(encoding="utf-8") as handle:
        first = ""
        for line in handle:
            if line.strip():
                first = line
                break
        if not first:
            return []
        if first.lstrip().startswith("["):
            return json.loads(first + handle.read())

        events = [json.loads(first)]
        for line_number, line in enumerate(handle, start=2):
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
        return events


def _resolve(spec: str):
    """Resolve ``package.module:callable`` from the invoking repository."""
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"{spec!r} must have the form package.module:callable")
    repository_root = str(Path.cwd())
    if repository_root not in sys.path:
        # Console entry points start with the virtualenv's bin directory on
        # sys.path. The adapter, however, belongs to the invoking repository.
        sys.path.insert(0, repository_root)
    value = importlib.import_module(module_name)
    for part in attribute.split("."):
        value = getattr(value, part)
    if not callable(value):
        raise TypeError(f"{spec!r} resolved to a non-callable object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="replaycheck")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    hazards_parser = commands.add_parser("hazards", help="inspect a JSON fixture")
    hazards_parser.add_argument("events", help="JSON array or JSON Lines file")

    check_parser = commands.add_parser(
        "check", help="replay an adapter from the current repository"
    )
    check_parser.add_argument(
        "--handler", required=True, help="handler as package.module:callable"
    )
    check_parser.add_argument("--events", required=True, help="JSON array or JSON Lines file")
    check_parser.add_argument("--invariant", help="invariant as package.module:callable")
    check_parser.add_argument("--setup", help="setup hook as package.module:callable")
    check_parser.add_argument(
        "--compare", action="append", metavar="EFFECT", help="effect to compare; repeatable"
    )
    check_parser.add_argument("--reorder", type=_non_negative_int, default=0)
    check_parser.add_argument("--max-schedules", type=_non_negative_int, default=200)
    check_parser.add_argument("--seed", type=int, default=0)
    check_parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable report"
    )
    check_parser.add_argument(
        "--fail-on-partial",
        action="store_true",
        help="return failure when the schedule budget samples rather than exhausts",
    )
    return parser


def main(argv=None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        events = _load(args.events)
        if args.command == "hazards":
            findings = hazards(events)
            for finding in findings:
                print(finding)

            missing = [finding.name for finding in findings if not finding.present]
            if missing:
                print()
                print(
                    f"{len(events)} events, and none of them exercise: "
                    + ", ".join(missing)
                )
            return 0

        report = check(
            _resolve(args.handler),
            events,
            invariant=_resolve(args.invariant) if args.invariant else None,
            setup=_resolve(args.setup) if args.setup else None,
            reorder=args.reorder,
            compare=args.compare,
            max_schedules=args.max_schedules,
            seed=args.seed,
        )
        print(json.dumps(report.as_dict(), sort_keys=True) if args.json else report.text())
        if not report:
            return 1
        return 1 if args.fail_on_partial and not report.complete else 0
    except (
        AttributeError,
        ImportError,
        json.JSONDecodeError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"replaycheck: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
