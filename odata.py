from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Any

from export_products import DEFAULT_USD_TO_RUB_RATE, parse_decimal, product_to_stock_price
from src.services.unf_1c.client import OneCODataClient
from src.services.unf_1c.nomenclature import load_supplier_nomenclature_items
from src.services.unf_1c.price_documents import build_price_document_payload
from src.services.unf_1c.registers import build_supplier_stock_records
from src.services.unf_1c.settings import ODataSyncError, OneCODataSettings
from src.services.unf_1c.types import NomenclatureSupplierItem, PriceUpdate
from src.utils import is_valid_value, split_batches
from three_logic_client import ThreeLogicApiError, ThreeLogicClient


class ThreeLogicLookup:
    def __init__(self, usd_to_rub_rate: Decimal) -> None:
        self.client = ThreeLogicClient()
        self.usd_to_rub_rate = usd_to_rub_rate
        self.product_id_cache: dict[str, dict[str, Any] | None] = {}
        self.product_id_batch_size = 50

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

    def preload_product_ids(self, product_ids: list[str]) -> None:
        normalized: list[str] = []
        for raw in product_ids:
            key = raw.strip()
            if not is_valid_value(key):
                continue
            if key not in normalized:
                normalized.append(key)

        if not normalized:
            return

        for chunk in self._split_chunks(normalized, self.product_id_batch_size):
            for key in chunk:
                self.product_id_cache.setdefault(key, None)

            try:
                products = self.client.iter_pricelist(
                    per_page=200,
                    filters={"product_ids": chunk},
                )
            except ThreeLogicApiError:
                continue

            for product in products:
                product_id = str(product.get("product_id", "")).strip()
                if product_id in self.product_id_cache:
                    self.product_id_cache[product_id] = product

    def find_by_product_id(self, product_id: str) -> dict[str, Any] | None:
        key = product_id.strip()
        if not is_valid_value(key):
            return None
        if key not in self.product_id_cache:
            self.product_id_cache[key] = self._load_exact(
                field_name="product_id",
                value=key,
                filters={"product_ids": [key]},
            )
        return self.product_id_cache[key]

    def find_price_update(self, item: NomenclatureSupplierItem) -> PriceUpdate | None:
        product = self.find_by_product_id(item.product_id)

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

        remain_text = str(stock_price.get("remain", "")).strip()
        remain_value: Decimal | None
        if remain_text:
            try:
                remain_value = parse_decimal(remain_text)
            except ValueError:
                remain_value = None
        else:
            remain_value = None

        return PriceUpdate(
            nomenclature_key=item.nomenclature.key,
            supplier_nomenclature_key=item.key,
            product_id=item.product_id,
            characteristic_key=item.characteristic_key,
            name=item.name,
            price_rub=price_rub,
            remain=remain_value,
            source_product=stock_price,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync 3Logic prices into 1C UNF via OData.")
    parser.add_argument("--dry-run", action="store_true", help="Do not create 1C documents, print summary only.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of 1C nomenclature records to process.")
    parser.add_argument("--batch-size", type=int, default=800, help="Number of rows per created price document.")
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
        supplier_nomenclature_items = load_supplier_nomenclature_items(client, settings, limit=args.limit)
    except ODataSyncError as error:
        raise SystemExit(f"Failed to read 1C OData data: {error}") from error

    print(f"Supplier nomenclature candidates: {len(supplier_nomenclature_items)}")

    # Batch-load product_ids to reduce API roundtrips to 3Logic.
    try:
        lookup.preload_product_ids([item.product_id for item in supplier_nomenclature_items])
    except ThreeLogicApiError as error:
        raise SystemExit(f"3Logic preload failed: {error}") from error

    updates: list[PriceUpdate] = []
    zero_stock_items: list[NomenclatureSupplierItem] = []
    not_found_count = 0
    for item in supplier_nomenclature_items:
        try:
            update = lookup.find_price_update(item)
        except ThreeLogicApiError as error:
            raise SystemExit(f"3Logic lookup failed: {error}") from error

        if update is None:
            not_found_count += 1
            zero_stock_items.append(item)
            continue
        updates.append(update)

    print(f"Matched in 3Logic: {len(updates)}")
    print(f"Not found in 3Logic: {not_found_count}")

    supplier_stock_records = build_supplier_stock_records(updates, zero_stock_items=zero_stock_items)

    if not updates and not supplier_stock_records:
        print("No price updates to apply.")
        return
    if not updates:
        print("No price updates to apply.")

    batches = split_batches(updates, args.batch_size)
    print(f"Document batches: {len(batches)}")

    if args.dry_run:
        print("Dry run mode: no documents created.")
        for sample in updates[:10]:
            print(
                f" - {sample.name or sample.product_id}: key={sample.nomenclature_key}, "
                f"priceRub={sample.price_rub}, remain={sample.remain}"
            )
        if supplier_stock_records:
            print("Supplier stock register records (dry-run):")
            for record in supplier_stock_records[:10]:
                print(f" - {record}")
        return

    created = 0
    for index, batch in enumerate(batches):
        payload = build_price_document_payload(
            updates=batch,
            settings=settings,
            batch_index=index,
            supplier_nomenclature_candidates_count=len(supplier_nomenclature_items),
            matched_count=len(updates),
            not_found_count=not_found_count,
        )
        try:
            client.create_price_document(payload)
        except ODataSyncError as error:
            raise SystemExit(f"Failed to create 1C price document for batch {index + 1}: {error}") from error
        created += 1

    print(f"Created price documents: {created}")

    if supplier_stock_records:
        created_stocks = 0
        for record in supplier_stock_records:
            try:
                client.upsert_supplier_stock_record(record)
            except ODataSyncError as error:
                raise SystemExit(f"Failed to write supplier stock register: {error}") from error
            created_stocks += 1
        print(f"Written supplier stock records: {created_stocks}")


if __name__ == "__main__":
    main()
