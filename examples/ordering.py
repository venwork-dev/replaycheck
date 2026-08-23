"""Out-of-order arrival: a shipment that overtakes its payment."""

from replaycheck import check

EVENTS = [
    {"type": "paid", "order_id": "order-771"},
    {"type": "shipped", "order_id": "order-771"},
]

OUTCOME = ["paid", "shipped"]


def fulfil(event, world):
    """Drops a shipment that arrives before its payment."""
    order = event["order_id"]
    if event["type"] == "paid":
        world.effect("paid", key=order, order=order)
    elif world.has("paid", order):
        world.effect("shipped", key=order, order=order)


def fulfil_tolerant(event, world):
    """Holds an early shipment and releases it once the payment lands."""
    order = event["order_id"]
    if event["type"] == "paid":
        world.effect("paid", key=order, order=order)
        if world.has("pending_ship", order):
            world.effect("shipped", key=order, order=order)
    elif world.has("paid", order):
        world.effect("shipped", key=order, order=order)
    else:
        world.effect("pending_ship", key=order, order=order)


def main() -> None:
    print("--- ordering guaranteed (reorder off) ---")
    print(check(fulfil, EVENTS).text())

    print("\n--- ordering not guaranteed, drops the early shipment ---")
    print(check(fulfil, EVENTS, reorder=1, compare=OUTCOME).text())

    print("\n--- ordering not guaranteed, holds the early shipment ---")
    print(check(fulfil_tolerant, EVENTS, reorder=1, compare=OUTCOME).text())


if __name__ == "__main__":
    main()
