from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass
class NomenclatureItem:
    key: str
    name: str


@dataclass
class NomenclatureSupplierItem:
    key: str
    nomenclature: NomenclatureItem
    product_id: str
    characteristic_key: str
    name: str


@dataclass
class PriceUpdate:
    nomenclature_key: str
    supplier_nomenclature_key: str
    product_id: str
    characteristic_key: str
    name: str
    price_rub: Decimal
    remain: Decimal | None
    source_product: dict[str, Any]
