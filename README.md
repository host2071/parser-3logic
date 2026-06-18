# 3Logic API Parser

Программа помогает в работе парсинга товаров для интернет магазина видеокарт [viabit.ru](https://viabit.ru/).

Python parser scaffold for loading products from the 3Logic OAPI and exporting them to CSV.

## Setup

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in API credentials:

```env
THREELOGIC_API_BASE_URL=https://oapi.3logic.ru
THREELOGIC_LOGIN=your_login
THREELOGIC_PASSWORD=your_password
THREELOGIC_TOKEN_CACHE=.3logic_tokens.json
```

3. Export products to CSV:

```powershell
python main.py
```

You can choose an output directory or file:

```powershell
python main.py --output-dir exports
python main.py --output-file exports/products_export.csv
```

Useful filters:

```powershell
python main.py --include-out-of-stock
python main.py --brand-ids 10,25,30
python main.py --product-category-ids 100
```

By default, the exporter requests only products in stock (`only_in_stock=true`)
from product category `979198`.

## FastAPI Server

Run the local HTTP server with uvicorn:

```powershell
uvicorn api:app --host 0.0.0.0 --port 8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

Product categories with IDs and CSV download links:

```text
http://127.0.0.1:8000/catalog/product-categories
```

Raw JSON list:

```text
http://127.0.0.1:8000/catalog/product-categories.json
```

Download products as CSV:

```text
http://127.0.0.1:8000/export/products
```

The default export uses product category `979198`. You can override it with one
category ID:

```text
http://127.0.0.1:8000/export/products?category_id=100
```

Useful query parameters:

```text
category_id=100
product_ids=123,456
brand_ids=10,25
price_category_id=5
usd_to_rub=75
limit=20
per_page=200
include_out_of_stock=true
return_on_order_products=true
no_description=true
no_photos=true
no_attributes=true
filename=products.csv
```

If the product price comes from 3Logic with `currency_iso_code=USD`, the exporter
converts it to rubles with the `usd_to_rub` rate. The default rate is `75`.
The category HTML page tries to load the current USD rate from the Central Bank
of Russia and adds it to each download link.

The API returns a CSV attachment and includes the number of exported products in
the `X-Exported-Products` response header. By default, the filename includes
the category ID, for example `products_export_category_123.csv`. You can override
it with the `filename` query parameter.

## 1C SOAP Server

The SOAP service is a separate entry point for 1C stock and price import. Run it
from the `parser3log` directory:

```powershell
uvicorn soap:app --host 0.0.0.0 --port 8001
```

WSDL URL for 1C:

```text
http://127.0.0.1:8001/ws/InterfaceVersion?wsdl
```

The SOAP service name and path are fixed as `InterfaceVersion` by default. You
can override the path with `SOAP_PATH`, but for 1C compatibility keep it as:

```env
SOAP_PATH=/ws/InterfaceVersion
```

Optional HTTP Basic authentication:

```env
SOAP_BASIC_USER=your_soap_user
SOAP_BASIC_PASSWORD=your_soap_password
```

If either `SOAP_BASIC_USER` or `SOAP_BASIC_PASSWORD` is empty, SOAP Basic auth is
disabled.

When SOAP Basic auth is enabled:
- `401 Unauthorized` is returned when the `Authorization` header is missing.
- `403 Forbidden` is returned when credentials are provided but invalid.

SOAP methods:

```text
GetInterfaceVersion()
GetStockPrices(categoryIds, priceCategoryId, includeOutOfStock, usdToRub)
GetStockPriceByPartnumber(partnumber, usdToRub)
GetStockPriceByBarcode(barcode, usdToRub)
```

`categoryIds` is a comma-separated list of 3Logic product category IDs, for
example `979198,100500`. Empty `categoryIds` is rejected so the service never
exports the whole catalog by accident.

Products are matched in 1C by `partnumber` and `barcode`. Each SOAP product item
contains:

```text
partnumber, barcode, productId, name, categoryId, price, currency, priceRub, remain, onOrder, updatedAt
```

`priceRub` uses the same USD-to-RUB conversion logic as the CSV exporter. If
`usdToRub` is empty, the default rate is `75`.

## CSV Template

The template is written with `,` as the delimiter and UTF-8 BOM encoding for Excel compatibility.
The exported file starts with the actual column names. Product attributes are appended
as dynamic columns after the base template columns:

```text
KIT ID*,Название товара*,Артикул,Описание товара,Штрихкод,Статус,...,Бренд,<Название характеристики 1>,<Название характеристики 2>
```

The parser appends product data after this header row.

## Exported Fields

The exporter fills fields that are present in the 3Logic product response: name, article, description, barcode, status, price, category, stock, external product ID, photos, package data, brand, and all product attributes. USD prices are converted to rubles, and package weight from the API is converted from kilograms to grams for the `Вес с упаковкой, г` column.
Fields that are absent in the API response remain empty.
