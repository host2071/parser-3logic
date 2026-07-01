from __future__ import annotations

from src.utils import is_valid_value

from .client import OneCODataClient
from .constants import (
    SUPPLIER_CHARACTERISTIC_FIELD,
    SUPPLIER_MAIN_NOMENCLATURE_FIELD,
    SUPPLIER_NOMENCLATURE_KEY_FIELD,
    SUPPLIER_PRODUCT_ID_FIELD,
    ZERO_GUID,
)
from .settings import OneCODataSettings
from .types import NomenclatureItem, NomenclatureSupplierItem


def load_supplier_nomenclature_items(
    client: OneCODataClient,
    settings: OneCODataSettings,
    limit: int | None,
) -> list[NomenclatureSupplierItem]:
    nomenclature_rows = client.list_entity(
        entity_name=settings.nomenclature_entity,
        select_fields=[
            SUPPLIER_NOMENCLATURE_KEY_FIELD,
            SUPPLIER_PRODUCT_ID_FIELD,
            SUPPLIER_MAIN_NOMENCLATURE_FIELD,
            SUPPLIER_CHARACTERISTIC_FIELD,
        ],
        limit=limit,
    )
    if not nomenclature_rows:
        return []

    items: list[NomenclatureSupplierItem] = []
    for row in nomenclature_rows:
        supplier_key = str(row.get(SUPPLIER_NOMENCLATURE_KEY_FIELD, "")).strip()
        main_key = str(row.get(SUPPLIER_MAIN_NOMENCLATURE_FIELD, "")).strip()
        if not supplier_key or not main_key:
            continue
        product_id = str(row.get(SUPPLIER_PRODUCT_ID_FIELD, "")).strip()
        if not is_valid_value(product_id):
            continue
        characteristic_key = str(row.get(SUPPLIER_CHARACTERISTIC_FIELD, "")).strip() or ZERO_GUID

        items.append(
            NomenclatureSupplierItem(
                key=supplier_key,
                nomenclature=NomenclatureItem(
                    key=main_key,
                    name=product_id,
                ),
                product_id=product_id,
                characteristic_key=characteristic_key,
                name=product_id,
            )
        )

    return items
