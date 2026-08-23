"""python -m replaycheck hazards <events.jsonl>"""

from __future__ import annotations

import json
import sys

from .hazards import hazards


def _load(path: str) -> list:
    events = []
    with open(path) as handle:
        text = handle.read().strip()
    if not text:
        return events
    if text.lstrip().startswith("["):
        return json.loads(text)
    for line in text.splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "hazards":
        print("usage: python -m replaycheck hazards <events.jsonl>", file=sys.stderr)
        return 2

    events = _load(argv[1])
    findings = hazards(events)
    for finding in findings:
        print(finding)

    missing = [f.name for f in findings if not f.present]
    if missing:
        print()
        print(f"{len(events)} events, and none of them exercise: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
