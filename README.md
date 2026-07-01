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

## 1C OData Sync (UNF prices)

Use `odata.py` to sync 3Logic prices and supplier stock to 1C UNF via OData.
Prices are written to `Document_УстановкаЦенНоменклатуры`; supplier stock is
written to `InformationRegister_ОстаткиТоваровПоставщиков`.

Required `.env` settings:

```env
ONEC_ODATA_BASE_URL=http://127.0.0.1/unf/odata/standard.odata
ONEC_ODATA_USER=
ONEC_ODATA_PASSWORD=
ONEC_PRICE_TYPE_GUID=
ONEC_MARKUP_PRICE_TYPE_GUID=
ONEC_MARKUP_PERCENT=10
ONEC_PRICE_DOC_COMMENT=Загрузка двух видов цен через OData
```

The UNF OData structure is fixed in `odata.py`: products are matched by
`Catalog_НоменклатураПоставщиков.Идентификатор` -> 3Logic `product_ids`.
Prices are written with `ВидыЦен` and `Запасы` table sections; supplier stock
uses `Catalog_НоменклатураПоставщиков.Ref_Key` as
`НоменклатураПоставщика_Key`.

Safe test run:

```powershell
python odata.py --dry-run --limit 10 --batch-size 100
```

Production run:

```powershell
python odata.py --batch-size 200
```

Cron example (every 30 minutes):

```cron
*/30 * * * * cd /path/to/parser3log && /path/to/python odata.py --batch-size 200 >> /var/log/parser3log-odata.log 2>&1
```

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
