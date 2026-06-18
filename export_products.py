from __future__ import annotations

import argparse
import csv
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from create_csv_template import PRODUCT_CSV_COLUMNS
from three_logic_client import ThreeLogicApiError, ThreeLogicClient


DEFAULT_OUTPUT_FILENAME = "products_export.csv"
DEFAULT_PRODUCT_CATEGORY_ID = "979198"
DEFAULT_USD_TO_RUB_RATE = Decimal("75")
ATTRIBUTE_TEMPLATE_COLUMNS = {
    "Характеристика (Задайте название)",
    "Размер",
    "Цвет",
}
BASE_PRODUCT_CSV_COLUMNS = [
    column for column in PRODUCT_CSV_COLUMNS if column not in ATTRIBUTE_TEMPLATE_COLUMNS
]


def export_products_to_csv(
    output_path: Path,
    products: Iterable[dict[str, Any]],
    usd_to_rub_rate: Any = DEFAULT_USD_TO_RUB_RATE,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        return write_products_to_csv(writer, products, usd_to_rub_rate)


def export_products_to_csv_bytes(
    products: Iterable[dict[str, Any]],
    usd_to_rub_rate: Any = DEFAULT_USD_TO_RUB_RATE,
) -> tuple[bytes, int]:
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer)
    count = write_products_to_csv(writer, products, usd_to_rub_rate)
    return csv_buffer.getvalue().encode("utf-8-sig"), count


def write_products_to_csv(
    writer: Any,
    products: Iterable[dict[str, Any]],
    usd_to_rub_rate: Any = DEFAULT_USD_TO_RUB_RATE,
) -> int:
    rate = parse_decimal(usd_to_rub_rate)
    product_list = list(products)
    attribute_columns = collect_attribute_columns(product_list)
    csv_columns = BASE_PRODUCT_CSV_COLUMNS + attribute_columns

    writer.writerow(csv_columns)

    for product in product_list:
        writer.writerow(product_to_csv_row(product, csv_columns, attribute_columns, rate))

    return len(product_list)


def product_to_csv_row(
    product: dict[str, Any],
    csv_columns: list[str],
    attribute_columns: list[str],
    usd_to_rub_rate: Decimal,
) -> list[str]:
    price = product_price_in_rub(product, usd_to_rub_rate)
    photos = product.get("photos")

    row = empty_product_row(csv_columns)
    set_column(row, csv_columns, "KIT ID*", value_or_empty(product.get("product_id")))
    set_column(
        row,
        csv_columns,
        "Название товара*",
        first_not_empty(
            product.get("product_name"),
            product.get("model"),
            product.get("partnumber"),
        ),
    )
    set_column(row, csv_columns, "Артикул", value_or_empty(product.get("partnumber")))
    set_column(row, csv_columns, "Описание товара", value_or_empty(product.get("description")))
    set_column(row, csv_columns, "Штрихкод", value_or_empty(product.get("barcode")))
    set_column(row, csv_columns, "Статус", product_status(product))
    set_column(row, csv_columns, "Цена до скидки, руб.", price)
    set_column(row, csv_columns, "Цена со скидкой, руб.", price)
    set_column(row, csv_columns, "Ставка НДС", "")
    set_column(row, csv_columns, "Категория 1-го уровня*", value_or_empty(product.get("price_category")))
    set_column(row, csv_columns, "Категория 2-го уровня", value_or_empty(product.get("product_category")))
    set_column(row, csv_columns, "Склад: Склад №1", value_or_empty(product.get("remain")))
    set_column(row, csv_columns, "Внешний ID: YML", value_or_empty(product.get("product_id")))
    set_column(row, csv_columns, "Изображения и видео", photo_urls(photos))
    set_column(row, csv_columns, "Количество упаковок", "1")
    set_column(row, csv_columns, "Высота упаковки, см", meters_to_centimeters(product.get("product_height")))
    set_column(row, csv_columns, "Ширина упаковки, см", meters_to_centimeters(product.get("product_width")))
    set_column(row, csv_columns, "Длина упаковки, см", meters_to_centimeters(product.get("product_length")))
    set_column(row, csv_columns, "Вес с упаковкой, г", kilograms_to_grams(product.get("product_length")))
    set_column(row, csv_columns, "Бренд", value_or_empty(product.get("brand_name")))

    fill_attributes(row, csv_columns, attribute_columns, product.get("attributes"))
    return row


def product_to_stock_price(
    product: dict[str, Any],
    usd_to_rub_rate: Decimal,
) -> dict[str, Any]:
    return {
        "partnumber": value_or_empty(product.get("partnumber")),
        "barcode": value_or_empty(product.get("barcode")),
        "productId": value_or_empty(product.get("product_id")),
        "name": first_not_empty(
            product.get("product_name"),
            product.get("model"),
            product.get("partnumber"),
        ),
        "categoryId": value_or_empty(product.get("product_category_id")),
        "price": value_or_empty(product.get("price")),
        "currency": value_or_empty(product.get("currency_iso_code")),
        "priceRub": product_price_in_rub(product, usd_to_rub_rate),
        "remain": value_or_empty(product.get("remain")),
        "onOrder": bool(product.get("on_order")),
        "updatedAt": value_or_empty(product.get("update_date")),
    }


def empty_product_row(csv_columns: list[str]) -> list[str]:
    return [""] * len(csv_columns)


def set_column(
    row: list[str],
    csv_columns: list[str],
    column_name: str,
    value: Any,
    occurrence: int = 0,
) -> None:
    seen = 0
    for index, current_column_name in enumerate(csv_columns):
        if current_column_name != column_name:
            continue
        if seen == occurrence:
            row[index] = value_or_empty(value)
            return
        seen += 1


def product_status(product: dict[str, Any]) -> str:
    remain = product.get("remain")
    on_order = product.get("on_order")

    if isinstance(remain, (int, float)) and remain > 0:
        return "Опубликован"
    if on_order:
        return "Опубликован"
    return "Скрыт"


def photo_urls(photos: Any) -> str:
    if not isinstance(photos, list):
        return ""

    urls: list[str] = []
    for photo in photos[:30]:
        if not isinstance(photo, dict):
            continue

        url = first_not_empty(photo.get("large_image_url"), photo.get("small_image_url"))
        if url:
            urls.append(url)

    return " ".join(urls)


def collect_attribute_columns(products: Iterable[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()

    for product in products:
        for name, _value in iter_attribute_values(product.get("attributes")):
            if name in seen:
                continue
            columns.append(name)
            seen.add(name)

    return columns


def fill_attributes(
    row: list[str],
    csv_columns: list[str],
    attribute_columns: list[str],
    attributes: Any,
) -> None:
    values_by_name: dict[str, list[str]] = {}
    for name, value in iter_attribute_values(attributes):
        values_by_name.setdefault(name, []).append(value)

    for name, values in values_by_name.items():
        if name not in attribute_columns:
            continue
        column_index = csv_columns.index(name, len(BASE_PRODUCT_CSV_COLUMNS))
        row[column_index] = "; ".join(values)


def iter_attribute_values(attributes: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(attributes, list):
        return

    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue

        name = value_or_empty(attribute.get("attribute_name"))
        value = value_or_empty(attribute.get("value"))
        unit = value_or_empty(attribute.get("unit"))
        if not name or not value:
            continue

        attribute_text = value
        if unit:
            attribute_text = f"{attribute_text} {unit}"

        yield name, attribute_text


def first_not_empty(*values: Any) -> str:
    for value in values:
        normalized = value_or_empty(value)
        if normalized:
            return normalized
    return ""


def value_or_empty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def product_price_in_rub(product: dict[str, Any], usd_to_rub_rate: Decimal) -> str:
    price = parse_optional_decimal(product.get("price"))
    if price is None:
        return value_or_empty(product.get("price"))

    currency = value_or_empty(product.get("currency_iso_code")).strip().upper()
    if currency == "USD":
        price *= usd_to_rub_rate

    return decimal_to_string(price)


def kilograms_to_grams(value: Any) -> str:
    weight = parse_optional_decimal(value)
    if weight is None:
        return value_or_empty(value)

    grams = weight * Decimal("1000")
    return decimal_to_string(grams)


def meters_to_centimeters(value: Any) -> str:
    meters = parse_optional_decimal(value)
    if meters is None:
        return value_or_empty(value)

    centimeters = meters * Decimal("100")
    return decimal_to_string(centimeters)


def parse_decimal(value: Any) -> Decimal:
    normalized = value_or_empty(value).strip().replace(",", ".")
    if not normalized:
        raise ValueError("Decimal value must not be empty.")

    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"Invalid decimal value: {value}") from error


def parse_optional_decimal(value: Any) -> Decimal | None:
    normalized = value_or_empty(value).strip().replace(",", ".")
    if not normalized:
        return None

    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def decimal_to_string(value: Decimal) -> str:
    value = value.quantize(Decimal("0.01"))
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def parse_id_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_filters(args: argparse.Namespace) -> dict[str, Any]:
    return build_product_filters(
        product_category_id=args.product_category_ids,
        product_ids=args.product_ids,
        brand_ids=args.brand_ids,
        price_category_id=args.price_category_id,
    )


def build_product_filters(
    product_category_id: str | None = None,
    product_ids: str | None = None,
    brand_ids: str | None = None,
    price_category_id: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}

    product_category_ids = parse_id_list(product_category_id) or [DEFAULT_PRODUCT_CATEGORY_ID]
    if len(product_category_ids) != 1:
        raise ValueError("Specify exactly one product category ID.")
    filters["product_category_ids"] = product_category_ids

    parsed_product_ids = parse_id_list(product_ids)
    if parsed_product_ids:
        filters["product_ids"] = parsed_product_ids

    parsed_brand_ids = parse_id_list(brand_ids)
    if parsed_brand_ids:
        filters["brand_ids"] = parsed_brand_ids

    if price_category_id:
        filters["price_category_id"] = price_category_id

    return filters


def resolve_output_path(output_dir: str | None, output_file: str | None) -> Path:
    if output_file:
        return Path(output_file)

    directory = Path(output_dir or ".")
    return directory / DEFAULT_OUTPUT_FILENAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export 3Logic products to CSV.")
    parser.add_argument("--output-dir", help="Directory where the CSV file should be created.")
    parser.add_argument("--output-file", help="Full CSV file path. Overrides --output-dir.")
    parser.add_argument("--per-page", type=int, default=200, choices=range(1, 201))
    parser.add_argument("--include-out-of-stock", action="store_true", help="Export all products instead of only products currently in stock.", )
    parser.add_argument("--return-on-order-products", action="store_true")
    parser.add_argument("--no-description", action="store_true")
    parser.add_argument("--no-photos", action="store_true")
    parser.add_argument("--no-attributes", action="store_true")
    parser.add_argument("--product-ids", help="Comma-separated product_id filter.")
    parser.add_argument("--price-category-id")
    parser.add_argument("--product-category-ids", help=f"Single product_category_id filter. Defaults to {DEFAULT_PRODUCT_CATEGORY_ID}.")
    parser.add_argument("--brand-ids", help="Comma-separated brand_id filter.")
    parser.add_argument(
        "--usd-to-rub-rate",
        default=str(DEFAULT_USD_TO_RUB_RATE),
        help=f"USD to RUB exchange rate. Defaults to {DEFAULT_USD_TO_RUB_RATE}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = resolve_output_path(args.output_dir, args.output_file)

    client = ThreeLogicClient()
    try:
        filters = build_filters(args)
        products = client.iter_products(
            per_page=args.per_page,
            only_in_stock=False if args.include_out_of_stock else True,
            return_on_order_products=True if args.return_on_order_products else None,
            add_attributes=not args.no_attributes,
            add_description=not args.no_description,
            add_photos=not args.no_photos,
            filters=filters,
        )
        exported_count = export_products_to_csv(output_path, products, args.usd_to_rub_rate)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    except ThreeLogicApiError as error:
        status = f" HTTP {error.status_code}" if error.status_code else ""
        raise SystemExit(f"3Logic product export failed{status}: {error}") from error

    print(f"Exported products: {exported_count}")
    print(f"CSV file: {output_path}")


if __name__ == "__main__":
    main()
