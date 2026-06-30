from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from export_products import DEFAULT_USD_TO_RUB_RATE, parse_decimal, product_to_stock_price
from three_logic_client import ThreeLogicApiError, ThreeLogicClient


class ODataSyncError(RuntimeError):
    """Raised when 1C OData sync cannot continue safely."""


def value_or_none(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def decimal_to_json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def is_valid_article(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    return normalized.lower() not in {"none", "null"}


ZERO_GUID = "00000000-0000-0000-0000-000000000000"
NOMENCLATURE_ENTITY = "Catalog_Номенклатура"
PRICE_DOCUMENT_ENTITY = "Document_УстановкаЦенНоменклатуры"
NOMENCLATURE_KEY_FIELD = "Ref_Key"
NOMENCLATURE_ARTICLE_FIELD = "Артикул"
PRICE_DOC_LINES_FIELD = "Запасы"
PRICE_DOC_LINE_NUMBER_FIELD = "LineNumber"
PRICE_DOC_LINE_NOMENCLATURE_FIELD = "Номенклатура_Key"
PRICE_DOC_LINE_CHARACTERISTIC_FIELD = "Характеристика_Key"
PRICE_DOC_LINE_PRICE_FIELD = "Цена"
PRICE_DOC_LINE_PRICE_TYPE_FIELD = "ВидЦены_Key"
PRICE_DOC_DATE_FIELD = "Date"
PRICE_DOC_POSTED_FIELD = "Posted"
PRICE_TYPES_FIELD = "ВидыЦен"
PRICE_TYPE_LINE_NUMBER_FIELD = "LineNumber"
PRICE_TYPE_FIELD = "ВидЦены_Key"


@dataclass(frozen=True)
class OneCODataSettings:
    base_url: str
    user: str
    password: str
    timeout: int
    page_size: int
    nomenclature_entity: str
    price_document_entity: str
    nomenclature_key_field: str
    nomenclature_article_field: str
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
            nomenclature_entity=NOMENCLATURE_ENTITY,
            price_document_entity=PRICE_DOCUMENT_ENTITY,
            nomenclature_key_field=NOMENCLATURE_KEY_FIELD,
            nomenclature_article_field=NOMENCLATURE_ARTICLE_FIELD,
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


class OneCODataClient:
    def __init__(self, settings: OneCODataSettings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.auth = (settings.user, settings.password)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            }
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(method, url, timeout=self.settings.timeout, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}

        if response.status_code >= 400:
            raise ODataSyncError(
                f"1C OData {method} failed {response.status_code}: {json.dumps(data, ensure_ascii=False)}"
            )

        if not isinstance(data, dict):
            raise ODataSyncError(f"Unexpected 1C OData response payload: {data!r}")
        return data

    def list_entity(
        self,
        entity_name: str,
        select_fields: list[str],
        limit: int | None = None,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        base_url = f"{self.settings.base_url}/{entity_name}"
        select_value = ",".join(select_fields)
        skip = 0
        items: list[dict[str, Any]] = []
        next_link: str | None = None

        while True:
            if next_link:
                url = next_link
            else:
                query = {
                    "$format": "json",
                    "$select": select_value,
                    "$top": str(self.settings.page_size),
                    "$skip": str(skip),
                }
                if filter_expr:
                    query["$filter"] = filter_expr
                url = f"{base_url}?{urlencode(query)}"

            data = self._request("GET", url)
            value = data.get("value")
            if not isinstance(value, list):
                raise ODataSyncError(f"1C OData entity {entity_name} response has no list in 'value'.")

            page_items = [item for item in value if isinstance(item, dict)]
            items.extend(page_items)

            if limit is not None and len(items) >= limit:
                return items[:limit]

            next_link_raw = data.get("@odata.nextLink")
            next_link = str(next_link_raw) if next_link_raw else None
            if not next_link:
                if len(page_items) < self.settings.page_size:
                    break
                skip += self.settings.page_size

        return items

    def create_price_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url}/{self.settings.price_document_entity}"
        return self._request("POST", url, json=payload)


@dataclass
class NomenclatureItem:
    key: str
    article: str
    name: str


@dataclass
class PriceUpdate:
    nomenclature_key: str
    article: str
    name: str
    price_rub: Decimal
    remain: str
    source_product: dict[str, Any]


class ThreeLogicLookup:
    def __init__(self, usd_to_rub_rate: Decimal) -> None:
        self.client = ThreeLogicClient()
        self.usd_to_rub_rate = usd_to_rub_rate
        self.partnumber_cache: dict[str, dict[str, Any] | None] = {}
        self.partnumber_batch_size = 50

    @staticmethod
    def _split_chunks(items: list[str], chunk_size: int) -> list[list[str]]:
        return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]

    def _load_exact(self, field_name: str, value: str, filters: dict[str, Any]) -> dict[str, Any] | None:
        expected = value.strip()
        try:
            products = self.client.iter_pricelist(
                per_page=200,
                filters=filters,
            )
        except ThreeLogicApiError:
            return None

        for product in products:
            if str(product.get(field_name, "")).strip() == expected:
                return product
        return None

    def preload_partnumbers(self, partnumbers: list[str]) -> None:
        normalized: list[str] = []
        for raw in partnumbers:
            key = raw.strip()
            if not is_valid_article(key):
                continue
            if key not in normalized:
                normalized.append(key)

        if not normalized:
            return

        for chunk in self._split_chunks(normalized, self.partnumber_batch_size):
            for key in chunk:
                self.partnumber_cache.setdefault(key, None)

            try:
                products = self.client.iter_pricelist(
                    per_page=200,
                    filters={"partnumbers": chunk},
                )
            except ThreeLogicApiError:
                continue

            for product in products:
                partnumber = str(product.get("partnumber", "")).strip()
                if partnumber in self.partnumber_cache:
                    self.partnumber_cache[partnumber] = product

    def find_by_partnumber(self, partnumber: str) -> dict[str, Any] | None:
        key = partnumber.strip()
        if not is_valid_article(key):
            return None
        if key not in self.partnumber_cache:
            self.partnumber_cache[key] = self._load_exact(
                field_name="partnumber",
                value=key,
                filters={"partnumber": key},
            )
        return self.partnumber_cache[key]

    def find_price_update(self, item: NomenclatureItem) -> PriceUpdate | None:
        product = self.find_by_partnumber(item.article)

        if product is None:
            return None

        stock_price = product_to_stock_price(product, self.usd_to_rub_rate)
        price_rub_text = str(stock_price.get("priceRub", "")).strip()
        if not price_rub_text:
            return None

        try:
            price_rub = parse_decimal(price_rub_text)
        except ValueError:
            return None

        return PriceUpdate(
            nomenclature_key=item.key,
            article=item.article,
            name=item.name,
            price_rub=price_rub,
            remain=str(stock_price.get("remain", "")),
            source_product=stock_price,
        )


def split_batches(items: list[PriceUpdate], batch_size: int) -> list[list[PriceUpdate]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def apply_markup(value: Decimal, percent: Decimal) -> Decimal:
    return (value * (Decimal("1") + percent / Decimal("100"))).quantize(Decimal("0.01"))


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
        line[settings.doc_line_characteristic_field] = ZERO_GUID
    if settings.doc_line_price_type_field:
        line[settings.doc_line_price_type_field] = price_type_guid
    return line


def build_document_comment(
    settings: OneCODataSettings,
    nomenclature_candidates_count: int,
    matched_count: int,
    not_found_count: int,
) -> str:
    lines = [
        f"Nomenclature candidates: {nomenclature_candidates_count}",
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
    nomenclature_candidates_count: int,
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
        nomenclature_candidates_count=nomenclature_candidates_count,
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


def load_nomenclature_items(
    client: OneCODataClient,
    settings: OneCODataSettings,
    limit: int | None,
) -> list[NomenclatureItem]:
    nomenclature_rows = client.list_entity(
        entity_name=settings.nomenclature_entity,
        select_fields=[
            settings.nomenclature_key_field,
            settings.nomenclature_article_field,
        ],
        limit=limit,
    )
    if not nomenclature_rows:
        return []

    items: list[NomenclatureItem] = []
    for row in nomenclature_rows:
        key = str(row.get(settings.nomenclature_key_field, "")).strip()
        if not key:
            continue
        article = str(row.get(settings.nomenclature_article_field, "")).strip()
        if not is_valid_article(article):
            continue

        items.append(
            NomenclatureItem(
                key=key,
                article=article,
                name=article,
            )
        )

    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync 3Logic prices into 1C UNF via OData.")
    parser.add_argument("--dry-run", action="store_true", help="Do not create 1C documents, print summary only.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of 1C nomenclature records to process.")
    parser.add_argument("--batch-size", type=int, default=200, help="Number of rows per created price document.")
    parser.add_argument(
        "--usd-to-rub-rate",
        default=str(DEFAULT_USD_TO_RUB_RATE),
        help=f"USD to RUB exchange rate. Defaults to {DEFAULT_USD_TO_RUB_RATE}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be greater than zero.")

    try:
        settings = OneCODataSettings.from_env()
        usd_to_rub_rate = parse_decimal(args.usd_to_rub_rate)
    except (ODataSyncError, ValueError) as error:
        raise SystemExit(str(error)) from error

    client = OneCODataClient(settings)
    lookup = ThreeLogicLookup(usd_to_rub_rate=usd_to_rub_rate)

    try:
        nomenclature_items = load_nomenclature_items(client, settings, limit=args.limit)
    except ODataSyncError as error:
        raise SystemExit(f"Failed to read 1C OData data: {error}") from error

    print(f"Nomenclature candidates: {len(nomenclature_items)}")

    # Batch-load partnumbers to reduce API roundtrips to 3Logic.
    try:
        lookup.preload_partnumbers([item.article for item in nomenclature_items])
    except ThreeLogicApiError as error:
        raise SystemExit(f"3Logic preload failed: {error}") from error

    updates: list[PriceUpdate] = []
    not_found_count = 0
    for item in nomenclature_items:
        try:
            update = lookup.find_price_update(item)
        except ThreeLogicApiError as error:
            raise SystemExit(f"3Logic lookup failed: {error}") from error

        if update is None:
            not_found_count += 1
            continue
        updates.append(update)

    print(f"Matched in 3Logic: {len(updates)}")
    print(f"Not found in 3Logic: {not_found_count}")

    if not updates:
        print("No price updates to apply.")
        return

    batches = split_batches(updates, args.batch_size)
    print(f"Document batches: {len(batches)}")

    if args.dry_run:
        print("Dry run mode: no documents created.")
        for sample in updates[:10]:
            print(
                f" - {sample.name or sample.article}: key={sample.nomenclature_key}, "
                f"priceRub={sample.price_rub}, remain={sample.remain}"
            )
        return

    created = 0
    for index, batch in enumerate(batches):
        payload = build_price_document_payload(
            updates=batch,
            settings=settings,
            batch_index=index,
            nomenclature_candidates_count=len(nomenclature_items),
            matched_count=len(updates),
            not_found_count=not_found_count,
        )
        try:
            client.create_price_document(payload)
        except ODataSyncError as error:
            raise SystemExit(f"Failed to create 1C price document for batch {index + 1}: {error}") from error
        created += 1

    print(f"Created price documents: {created}")


if __name__ == "__main__":
    main()
