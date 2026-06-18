from __future__ import annotations

import base64
import os
import sys
import types
from decimal import Decimal
from hmac import compare_digest
from io import BytesIO, StringIO
from collections.abc import Iterable, Iterator, MutableSet, Sequence
from typing import Any, Iterable
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from export_products import DEFAULT_USD_TO_RUB_RATE, parse_decimal, parse_id_list, product_to_stock_price
from three_logic_client import ThreeLogicApiError, ThreeLogicClient, ThreeLogicCredentialsError


INTERFACE_VERSION = "1.0"
SOAP_NAMESPACE = "urn:InterfaceVersion"
DEFAULT_SOAP_PATH = "/ws/InterfaceVersion"

load_dotenv()


def install_spyne_six_shim() -> None:
    """Provide the bits of spyne.util.six that spyne expects on Python 3.12."""
    if "spyne.util.six" in sys.modules:
        return

    six = types.ModuleType("spyne.util.six")
    six.__path__ = []  # type: ignore[attr-defined]
    six.PY = sys.version_info[0]
    six.PY2 = False
    six.PY3 = True
    six.PY34 = True
    six.string_types = (str,)
    six.integer_types = (int,)
    six.class_types = (type,)
    six.text_type = str
    six.binary_type = bytes
    six.BytesIO = BytesIO
    six.StringIO = StringIO
    six.get_function_name = lambda func: getattr(func, "__name__", repr(func))  # type: ignore[assignment]
    six.add_metaclass = lambda metaclass: (  # type: ignore[assignment]
        lambda cls: metaclass(cls.__name__, cls.__bases__, dict(cls.__dict__))
    )
    six.with_metaclass = lambda metaclass, base=object: metaclass("TemporaryClass", (base,), {})  # type: ignore[assignment]

    moves = types.ModuleType("spyne.util.six.moves")
    moves.__path__ = []  # type: ignore[attr-defined]

    collections_abc = types.ModuleType("spyne.util.six.moves.collections_abc")
    collections_abc.Iterable = Iterable
    collections_abc.Iterator = Iterator
    collections_abc.MutableSet = MutableSet
    collections_abc.Sequence = Sequence

    http_cookies = types.ModuleType("spyne.util.six.moves.http_cookies")
    http_cookies.SimpleCookie = SimpleCookie

    urllib = types.ModuleType("spyne.util.six.moves.urllib")
    urllib.__path__ = []  # type: ignore[attr-defined]

    urllib_parse = types.ModuleType("spyne.util.six.moves.urllib.parse")
    urllib_parse.quote = quote
    urllib_parse.unquote = unquote
    urllib_parse.urlencode = urlencode

    urllib_request = types.ModuleType("spyne.util.six.moves.urllib.request")
    urllib_request.Request = Request
    urllib_request.urlopen = urlopen

    urllib_error = types.ModuleType("spyne.util.six.moves.urllib.error")
    urllib_error.HTTPError = HTTPError

    builtins_mod = types.ModuleType("spyne.util.six.moves.builtins")
    builtins_mod.exec = exec
    builtins_mod.print = print

    sys.modules["spyne.util.six"] = six
    sys.modules["spyne.util.six.moves"] = moves
    sys.modules["spyne.util.six.moves.collections_abc"] = collections_abc
    sys.modules["spyne.util.six.moves.http_cookies"] = http_cookies
    sys.modules["spyne.util.six.moves.urllib"] = urllib
    sys.modules["spyne.util.six.moves.urllib.parse"] = urllib_parse
    sys.modules["spyne.util.six.moves.urllib.request"] = urllib_request
    sys.modules["spyne.util.six.moves.urllib.error"] = urllib_error
    sys.modules["spyne.util.six.moves.builtins"] = builtins_mod


install_spyne_six_shim()

from spyne import Application, Array, Boolean, ComplexModel, Fault, ServiceBase, Unicode, rpc
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication
from starlette.middleware.wsgi import WSGIMiddleware


class StockPriceItem(ComplexModel):
    __namespace__ = SOAP_NAMESPACE

    partnumber = Unicode
    barcode = Unicode
    productId = Unicode
    name = Unicode
    categoryId = Unicode
    price = Unicode
    currency = Unicode
    priceRub = Unicode
    remain = Unicode
    onOrder = Boolean
    updatedAt = Unicode


class InterfaceVersion(ServiceBase):
    @rpc(_returns=Unicode)
    def GetInterfaceVersion(ctx) -> str:
        return INTERFACE_VERSION

    @rpc(Unicode, Unicode, Boolean, Unicode, _returns=Array(StockPriceItem))
    def GetStockPrices(
        ctx,
        categoryIds: str,
        priceCategoryId: str | None = None,
        includeOutOfStock: bool = False,
        usdToRub: str | None = None,
    ) -> list[StockPriceItem]:
        category_ids = require_id_list(categoryIds, "categoryIds")
        filters = {"product_category_ids": category_ids}
        if priceCategoryId:
            filters["price_category_id"] = priceCategoryId

        products = load_products(filters=filters, include_out_of_stock=includeOutOfStock)
        return to_stock_price_items(products, usdToRub)

    @rpc(Unicode, Unicode, _returns=Array(StockPriceItem))
    def GetStockPriceByPartnumber(ctx, partnumber: str, usdToRub: str | None = None) -> list[StockPriceItem]:
        normalized = require_text(partnumber, "partnumber")
        products = load_products_by_exact_field(
            field_name="partnumber",
            value=normalized,
            direct_filters=(
                {"partnumbers": [normalized]},
                {"partnumber": normalized},
            ),
        )
        return to_stock_price_items(products, usdToRub)

    @rpc(Unicode, Unicode, _returns=Array(StockPriceItem))
    def GetStockPriceByBarcode(ctx, barcode: str, usdToRub: str | None = None) -> list[StockPriceItem]:
        normalized = require_text(barcode, "barcode")
        products = load_products_by_exact_field(
            field_name="barcode",
            value=normalized,
            direct_filters=(
                {"barcodes": [normalized]},
                {"barcode": normalized},
            ),
        )
        return to_stock_price_items(products, usdToRub)


def load_products(
    filters: dict[str, Any],
    include_out_of_stock: bool = False,
    per_page: int = 200,
) -> Iterable[dict[str, Any]]:
    return ThreeLogicClient().iter_products(
        per_page=per_page,
        only_in_stock=False if include_out_of_stock else True,
        add_attributes=False,
        add_description=False,
        add_photos=False,
        filters=filters,
    )


def load_products_by_exact_field(
    field_name: str,
    value: str,
    direct_filters: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_value = value.strip()
    for filters in direct_filters:
        products = [
            product
            for product in load_products(filters=filters, include_out_of_stock=True)
            if str(product.get(field_name, "")).strip() == expected_value
        ]
        if products:
            return products

    return []


def to_stock_price_items(products: Iterable[dict[str, Any]], usd_to_rub: str | None) -> list[StockPriceItem]:
    rate = parse_usd_to_rub_rate(usd_to_rub)
    items: list[StockPriceItem] = []

    try:
        for product in products:
            items.append(StockPriceItem(**product_to_stock_price(product, rate)))
    except ThreeLogicCredentialsError as error:
        raise Fault(faultcode="Client.Auth", faultstring=str(error)) from error
    except ThreeLogicApiError as error:
        status = f" HTTP {error.status_code}" if error.status_code else ""
        raise Fault(faultcode="Server.ThreeLogic", faultstring=f"3Logic request failed{status}: {error}") from error
    except ValueError as error:
        raise Fault(faultcode="Client.Validation", faultstring=str(error)) from error

    return items


def parse_usd_to_rub_rate(value: str | None) -> Decimal:
    if not value:
        return DEFAULT_USD_TO_RUB_RATE
    try:
        return parse_decimal(value)
    except ValueError as error:
        raise Fault(faultcode="Client.Validation", faultstring=str(error)) from error


def require_id_list(value: str | None, field_name: str) -> list[str]:
    values = parse_id_list(value)
    if not values:
        raise Fault(faultcode="Client.Validation", faultstring=f"{field_name} must not be empty.")
    return values


def require_text(value: str | None, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise Fault(faultcode="Client.Validation", faultstring=f"{field_name} must not be empty.")
    return normalized


def soap_path() -> str:
    path = os.getenv("SOAP_PATH", DEFAULT_SOAP_PATH).strip() or DEFAULT_SOAP_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or DEFAULT_SOAP_PATH


def soap_credentials() -> tuple[str, str] | None:
    user = os.getenv("SOAP_BASIC_USER", "").strip()
    password = os.getenv("SOAP_BASIC_PASSWORD", "").strip()
    if not user or not password:
        return None
    return user, password


soap_application = Application(
    [InterfaceVersion],
    tns=SOAP_NAMESPACE,
    name="InterfaceVersion",
    in_protocol=Soap11(validator="lxml"),
    out_protocol=Soap11(),
)

app = FastAPI(title="3Logic 1C SOAP InterfaceVersion")


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    credentials = soap_credentials()
    if not credentials or not request.url.path.startswith(soap_path()):
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if is_valid_basic_auth(auth_header, credentials):
        return await call_next(request)

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="InterfaceVersion"'},
    )


app.mount(soap_path(), WSGIMiddleware(WsgiApplication(soap_application)))


def is_valid_basic_auth(auth_header: str, credentials: tuple[str, str]) -> bool:
    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False

    user, _, password = decoded.partition(":")
    expected_user, expected_password = credentials
    return compare_digest(user, expected_user) and compare_digest(password, expected_password)
