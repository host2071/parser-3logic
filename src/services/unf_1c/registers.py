from __future__ import annotations

from typing import Any

from src.utils import decimal_to_json_number

from .constants import SUPPLIER_STOCK_OWNER_FIELD, SUPPLIER_STOCK_QTY_FIELD
from .types import PriceUpdate


def build_supplier_stock_records(updates: list[PriceUpdate]) -> list[dict[str, Any]]:
    records_by_key: dict[str, dict[str, Any]] = {}
    for update in updates:
        if update.remain is None:
            continue
        key = update.supplier_nomenclature_key
        records_by_key[key] = {
            SUPPLIER_STOCK_OWNER_FIELD: key,
            SUPPLIER_STOCK_QTY_FIELD: decimal_to_json_number(update.remain),
        }
    return list(records_by_key.values())
