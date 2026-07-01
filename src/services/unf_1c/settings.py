from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

from dotenv import load_dotenv

from export_products import parse_decimal

from .constants import (
    PRICE_DOC_DATE_FIELD,
    PRICE_DOC_LINE_CHARACTERISTIC_FIELD,
    PRICE_DOC_LINE_NOMENCLATURE_FIELD,
    PRICE_DOC_LINE_NUMBER_FIELD,
    PRICE_DOC_LINE_PRICE_FIELD,
    PRICE_DOC_LINE_PRICE_TYPE_FIELD,
    PRICE_DOC_LINES_FIELD,
    PRICE_DOC_POSTED_FIELD,
    PRICE_DOCUMENT_ENTITY,
    SUPPLIER_NOMENCLATURE_ENTITY,
)


class ODataSyncError(RuntimeError):
    """Raised when 1C OData sync cannot continue safely."""


def value_or_none(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


@dataclass(frozen=True)
class OneCODataSettings:
    base_url: str
    user: str
    password: str
    timeout: int
    page_size: int
    nomenclature_entity: str
    price_document_entity: str
    doc_lines_field: str
    doc_line_number_field: str | None
    doc_line_nomenclature_field: str
    doc_line_characteristic_field: str | None
    doc_line_price_field: str
    doc_line_price_type_field: str | None
    doc_price_type_header_field: str | None
    doc_organization_field: str | None
    doc_number_field: str | None
    doc_date_field: str | None
    doc_posted_field: str | None
    doc_comment: str | None
    price_type_guid: str | None
    markup_price_type_guid: str | None
    markup_percent: Decimal

    @classmethod
    def from_env(cls) -> "OneCODataSettings":
        load_dotenv()

        base_url = value_or_none(os.getenv("ONEC_ODATA_BASE_URL"))
        user = value_or_none(os.getenv("ONEC_ODATA_USER"))
        password = value_or_none(os.getenv("ONEC_ODATA_PASSWORD"))

        if not base_url or not user or not password:
            raise ODataSyncError(
                "Set ONEC_ODATA_BASE_URL, ONEC_ODATA_USER and ONEC_ODATA_PASSWORD in .env."
            )

        settings = cls(
            base_url=base_url.rstrip("/"),
            user=user,
            password=password,
            timeout=int(os.getenv("ONEC_ODATA_TIMEOUT", "30")),
            page_size=max(int(os.getenv("ONEC_ODATA_PAGE_SIZE", "200")), 1),
            nomenclature_entity=SUPPLIER_NOMENCLATURE_ENTITY,
            price_document_entity=PRICE_DOCUMENT_ENTITY,
            doc_lines_field=PRICE_DOC_LINES_FIELD,
            doc_line_number_field=PRICE_DOC_LINE_NUMBER_FIELD,
            doc_line_nomenclature_field=PRICE_DOC_LINE_NOMENCLATURE_FIELD,
            doc_line_characteristic_field=PRICE_DOC_LINE_CHARACTERISTIC_FIELD,
            doc_line_price_field=PRICE_DOC_LINE_PRICE_FIELD,
            doc_line_price_type_field=PRICE_DOC_LINE_PRICE_TYPE_FIELD,
            doc_price_type_header_field=None,
            doc_organization_field=None,
            doc_number_field=None,
            doc_date_field=PRICE_DOC_DATE_FIELD,
            doc_posted_field=PRICE_DOC_POSTED_FIELD,
            doc_comment=value_or_none(os.getenv("ONEC_PRICE_DOC_COMMENT")),
            price_type_guid=value_or_none(os.getenv("ONEC_PRICE_TYPE_GUID")),
            markup_price_type_guid=value_or_none(os.getenv("ONEC_MARKUP_PRICE_TYPE_GUID")),
            markup_percent=parse_decimal(os.getenv("ONEC_MARKUP_PERCENT", "10")),
        )

        if not settings.price_type_guid:
            raise ODataSyncError("Set ONEC_PRICE_TYPE_GUID in .env (price type GUID is required).")
        if not settings.markup_price_type_guid:
            raise ODataSyncError("Set ONEC_MARKUP_PRICE_TYPE_GUID in .env (markup price type GUID is required).")

        return settings
