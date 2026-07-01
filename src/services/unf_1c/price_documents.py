from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from src.utils import apply_markup, decimal_to_json_number

from .constants import (
    PRICE_TYPE_FIELD,
    PRICE_TYPE_LINE_NUMBER_FIELD,
    PRICE_TYPES_FIELD,
    ZERO_GUID,
)
from .settings import OneCODataSettings
from .types import PriceUpdate


def build_price_type_lines(settings: OneCODataSettings) -> list[dict[str, Any]]:
    price_type_guids = [settings.price_type_guid, settings.markup_price_type_guid]
    lines: list[dict[str, Any]] = []
    for line_index, price_type_guid in enumerate(price_type_guids, start=1):
        if not price_type_guid:
            continue
        line: dict[str, Any] = {
            PRICE_TYPE_FIELD: price_type_guid,
            PRICE_TYPE_LINE_NUMBER_FIELD: str(line_index),
        }
        lines.append(line)
    return lines


def build_stock_price_line(
    update: PriceUpdate,
    settings: OneCODataSettings,
    line_index: int,
    price_type_guid: str,
    price_rub: Decimal,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        settings.doc_line_nomenclature_field: update.nomenclature_key,
        settings.doc_line_price_field: decimal_to_json_number(price_rub),
    }
    if settings.doc_line_number_field:
        line[settings.doc_line_number_field] = str(line_index)
    if settings.doc_line_characteristic_field:
        line[settings.doc_line_characteristic_field] = update.characteristic_key or ZERO_GUID
    if settings.doc_line_price_type_field:
        line[settings.doc_line_price_type_field] = price_type_guid
    return line


def build_document_comment(
    settings: OneCODataSettings,
    supplier_nomenclature_candidates_count: int,
    matched_count: int,
    not_found_count: int,
) -> str:
    lines = [
        f"Supplier nomenclature candidates: {supplier_nomenclature_candidates_count}",
        f"Matched in 3Logic: {matched_count}",
        f"Not found in 3Logic: {not_found_count}",
    ]
    if settings.doc_comment:
        lines.append(settings.doc_comment)
    return "\n".join(lines)


def build_price_document_payload(
    updates: list[PriceUpdate],
    settings: OneCODataSettings,
    batch_index: int,
    supplier_nomenclature_candidates_count: int,
    matched_count: int,
    not_found_count: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    now = datetime.now()

    if settings.doc_date_field:
        payload[settings.doc_date_field] = now.replace(microsecond=0).isoformat()
    if settings.doc_posted_field:
        payload[settings.doc_posted_field] = True
    payload["Комментарий"] = build_document_comment(
        settings=settings,
        supplier_nomenclature_candidates_count=supplier_nomenclature_candidates_count,
        matched_count=matched_count,
        not_found_count=not_found_count,
    )

    payload[PRICE_TYPES_FIELD] = build_price_type_lines(settings)

    lines: list[dict[str, Any]] = []
    line_index = 1
    for update in updates:
        if settings.price_type_guid:
            lines.append(
                build_stock_price_line(
                    update=update,
                    settings=settings,
                    line_index=line_index,
                    price_type_guid=settings.price_type_guid,
                    price_rub=update.price_rub,
                )
            )
            line_index += 1

        if settings.markup_price_type_guid:
            lines.append(
                build_stock_price_line(
                    update=update,
                    settings=settings,
                    line_index=line_index,
                    price_type_guid=settings.markup_price_type_guid,
                    price_rub=apply_markup(update.price_rub, settings.markup_percent),
                )
            )
            line_index += 1

    payload[settings.doc_lines_field] = lines
    return payload
