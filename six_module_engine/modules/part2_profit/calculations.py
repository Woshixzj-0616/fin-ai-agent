"""Pure Decimal calculation rules shared by the module-two workflow."""

from decimal import Decimal


def gross_margin_percent(revenue: Decimal | None, cost: Decimal | None) -> Decimal | None:
    if revenue is None or cost is None or revenue <= 0:
        return None
    return (revenue - cost) / revenue * Decimal(100)


def signed_profit_effect(change: Decimal, multiplier: Decimal) -> Decimal:
    """Apply the statement sign convention to a reported amount change."""
    return change * multiplier


def amount_change(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None:
        return None
    return current - previous
