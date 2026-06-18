from __future__ import annotations

from decimal import Decimal
from html import escape
from itertools import islice
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from export_products import (
    DEFAULT_OUTPUT_FILENAME,
    DEFAULT_PRODUCT_CATEGORY_ID,
    DEFAULT_USD_TO_RUB_RATE,
    build_product_filters,
    export_products_to_csv_bytes,
)
from three_logic_client import ThreeLogicApiError, ThreeLogicClient, ThreeLogicCredentialsError


app = FastAPI(title="3Logic Product Exporter")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/catalog/product-categories")
def product_categories() -> HTMLResponse:
    categories = load_product_categories()
    usd_to_rub_rate = fetch_usd_to_rub_rate()
    return HTMLResponse(render_product_categories_page(categories, usd_to_rub_rate))


@app.get("/catalog/product-categories.json")
def product_categories_json() -> list[dict[str, Any]]:
    return load_product_categories()


def load_product_categories() -> list[dict[str, Any]]:
    try:
        return ThreeLogicClient().list_product_categories()
    except ThreeLogicCredentialsError as error:
        raise credentials_http_error() from error
    except ThreeLogicApiError as error:
        raise three_logic_http_error("3Logic product categories request failed", error) from error


@app.get("/export/products")
def export_products(
    category_id: str = Query(DEFAULT_PRODUCT_CATEGORY_ID, description="Single product_category_id."),
    product_ids: str | None = Query(None, description="Comma-separated product_id list."),
    brand_ids: str | None = Query(None, description="Comma-separated brand_id list."),
    price_category_id: str | None = Query(None),
    limit: int | None = Query(None, ge=1),
    per_page: int = Query(200, ge=1, le=200),
    include_out_of_stock: bool = False,
    return_on_order_products: bool | None = None,
    no_description: bool = False,
    no_photos: bool = False,
    no_attributes: bool = False,
    usd_to_rub: str = Query(str(DEFAULT_USD_TO_RUB_RATE), description="USD to RUB exchange rate."),
    filename: str | None = Query(None),
) -> Response:
    try:
        filters = build_product_filters(
            product_category_id=category_id,
            product_ids=product_ids,
            brand_ids=brand_ids,
            price_category_id=price_category_id,
        )
        products = load_products(
            filters=filters,
            limit=limit,
            per_page=per_page,
            include_out_of_stock=include_out_of_stock,
            return_on_order_products=return_on_order_products,
            no_description=no_description,
            no_photos=no_photos,
            no_attributes=no_attributes,
        )
        csv_bytes, exported_count = export_products_to_csv_bytes(products, usd_to_rub)
    except ThreeLogicCredentialsError as error:
        raise credentials_http_error() from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ThreeLogicApiError as error:
        raise three_logic_http_error("3Logic product export failed", error) from error

    safe_name = export_filename(category_id, filename)
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Exported-Products": str(exported_count),
        },
    )


def load_products(
    filters: dict[str, Any],
    limit: int | None,
    per_page: int,
    include_out_of_stock: bool,
    return_on_order_products: bool | None,
    no_description: bool,
    no_photos: bool,
    no_attributes: bool,
) -> Iterable[dict[str, Any]]:
    client = ThreeLogicClient()
    products = client.iter_products(
        per_page=per_page,
        only_in_stock=False if include_out_of_stock else True,
        return_on_order_products=return_on_order_products,
        add_attributes=not no_attributes,
        add_description=not no_description,
        add_photos=not no_photos,
        filters=filters,
    )

    if limit:
        return islice(products, limit)
    return products


def safe_filename(value: str) -> str:
    cleaned = "".join(char for char in value if char.isalnum() or char in {"-", "_", "."})
    if not cleaned or cleaned in {".", ".."}:
        return DEFAULT_OUTPUT_FILENAME
    return cleaned


def export_filename(category_id: str, filename: str | None) -> str:
    if filename:
        return safe_filename(filename)

    safe_category_id = safe_filename(category_id)
    return f"products_export_category_{safe_category_id}.csv"


def render_product_categories_page(
    categories: list[dict[str, Any]],
    usd_to_rub_rate: Decimal,
) -> str:
    rows = "\n".join(render_category_row(category, usd_to_rub_rate) for category in categories)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Категории товаров 3Logic</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px 10px; text-align: left; }}
    th {{ background: #f0f4f8; }}
    a.download {{ color: #0b69a3; font-weight: 600; }}
    .muted {{ color: #52606d; }}
  </style>
</head>
<body>
  <h1>Категории товаров 3Logic</h1>
  <p class="muted">Найдено категорий: {len(categories)}. Курс USD для пересчета цен: {escape(decimal_to_url_value(usd_to_rub_rate))} руб. Нажмите на ссылку, чтобы скачать CSV товаров выбранной категории.</p>
  <table>
    <thead>
      <tr>
        <th>ID категории</th>
        <th>Название</th>
        <th>Скачать товары</th>
      </tr>
    </thead>
    <tbody>
{rows}
    </tbody>
  </table>
</body>
</html>
"""


def render_category_row(category: dict[str, Any], usd_to_rub_rate: Decimal) -> str:
    category_id = str(category.get("product_category_id", "")).strip()
    name = str(category.get("name", "")).strip()
    download_url = f"/export/products?{urlencode({'category_id': category_id, 'usd_to_rub': decimal_to_url_value(usd_to_rub_rate)})}"
    return f"""      <tr>
        <td>{escape(category_id)}</td>
        <td>{escape(name)}</td>
        <td><a class="download" href="{escape(download_url)}">Скачать CSV</a></td>
      </tr>"""


def fetch_usd_to_rub_rate(default: Decimal = DEFAULT_USD_TO_RUB_RATE) -> Decimal:
    try:
        with urlopen("https://www.cbr.ru/scripts/XML_daily.asp", timeout=5) as response:
            xml = response.read()
        root = ElementTree.fromstring(xml)
        for valute in root.findall("Valute"):
            char_code = valute.findtext("CharCode")
            if char_code != "USD":
                continue
            nominal = Decimal((valute.findtext("Nominal") or "1").replace(",", "."))
            value = Decimal((valute.findtext("Value") or "").replace(",", "."))
            if nominal <= 0 or value <= 0:
                return default
            return value / nominal
    except Exception:
        return default

    return default


def decimal_to_url_value(value: Decimal) -> str:
    value = value.quantize(Decimal("0.01"))
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def three_logic_http_error(message: str, error: ThreeLogicApiError) -> HTTPException:
    status = f" HTTP {error.status_code}" if error.status_code else ""
    return HTTPException(status_code=502, detail=f"{message}{status}: {error}")


def credentials_http_error() -> HTTPException:
    return HTTPException(status_code=403, detail="Access denied: please set the password in .env.")
