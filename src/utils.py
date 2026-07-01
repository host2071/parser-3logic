from __future__ import annotations

from decimal import Decimal
from typing import TypeVar


T = TypeVar("T")


def decimal_to_json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def apply_markup(value: Decimal, percent: Decimal) -> Decimal:
    return (value * (Decimal("1") + percent / Decimal("100"))).quantize(Decimal("0.01"))


def is_valid_value(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    return normalized.lower() not in {"none", "null"}


def split_batches(items: list[T], batch_size: int) -> list[list[T]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]
