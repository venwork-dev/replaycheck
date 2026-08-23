"""The demo: a payment handler that looks fine and charges twice on replay."""

from replaycheck import check

EVENTS = [
    {"event_id": "e1", "order_id": "order-771", "amount": 4200},
    {"event_id": "e2", "order_id": "order-772", "amount": 1500},
]


def charged_at_most_once(world):
    for _, data in world.effects("charge"):
        order = data["order"]
        assert world.count("charge", order=order) <= 1, f"{order} charged twice"
    return True


def process_order(event, world):
    """Looks correct. Crash between the two writes and the card is charged twice."""
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def process_order_fixed(event, world):
    """One change: the charge sink is keyed, so the replay is suppressed."""
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("charge", key=order, order=order, amount=event["amount"])
    world.effect("paid", key=order, order=order)


def process_order_reordered(event, world):
    """The other direction: crash after marking paid and the charge is lost."""
    order = event["order_id"]
    if world.has("paid", order):
        return
    world.effect("paid", key=order, order=order)
    world.effect("charge", key=order, order=order, amount=event["amount"])


def main() -> None:
    cases = (
        ("charges twice", process_order),
        ("never charges", process_order_reordered),
        ("correct", process_order_fixed),
    )
    for label, handler in cases:
        report = check(handler, EVENTS, invariant=charged_at_most_once)
        print(f"--- {label} ---")
        print(report.text())
        print()


if __name__ == "__main__":
    main()
