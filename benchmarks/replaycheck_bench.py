"""Small, dependency-free scaling benchmark for replaycheck."""

from __future__ import annotations

import argparse
import time

from replaycheck import check


def keyed_handler(event, world):
    order = event["order_id"]
    world.effect("posted", key=order, order=order, amount=event["amount"])


def benchmark(event_count: int, max_schedules: int) -> tuple[float, int, int]:
    events = [
        {"order_id": f"order-{index}", "amount": index + 1}
        for index in range(event_count)
    ]
    started = time.perf_counter()
    report = check(keyed_handler, events, max_schedules=max_schedules)
    elapsed = time.perf_counter() - started
    if not report:
        raise RuntimeError(report.text())
    return elapsed, report.schedules_run, report.schedules_available


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=[100, 400, 800],
        help="event counts to benchmark (default: 100 400 800)",
    )
    parser.add_argument(
        "--max-schedules",
        type=int,
        default=200,
        help="schedule budget per run (default: 200)",
    )
    args = parser.parse_args()
    print("events  seconds  schedules  available")
    for size in args.sizes:
        elapsed, schedules, available = benchmark(size, args.max_schedules)
        print(f"{size:>6}  {elapsed:>7.3f}  {schedules:>9}  {available:>9}")


if __name__ == "__main__":
    main()
