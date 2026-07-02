from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import requests

from .constants import SUPPLIER_STOCK_OWNER_FIELD, SUPPLIER_STOCK_QTY_FIELD, SUPPLIER_STOCK_REGISTER_ENTITY
from .settings import ODataSyncError, OneCODataSettings


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

    def create_entity(self, entity_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url}/{entity_name}"
        return self._request("POST", url, json=payload)

    def update_entity(self, entity_name: str, key_expr: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url}/{entity_name}({key_expr})"
        return self._request("PATCH", url, json=payload)

    def create_price_document(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.create_entity(self.settings.price_document_entity, payload)

    def create_supplier_stock_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.create_entity(SUPPLIER_STOCK_REGISTER_ENTITY, payload)

    def update_supplier_stock_record(self, supplier_nomenclature_key: str, quantity: int | float) -> dict[str, Any]:
        return self.update_entity(
            SUPPLIER_STOCK_REGISTER_ENTITY,
            key_expr=f"guid'{supplier_nomenclature_key}'",
            payload={SUPPLIER_STOCK_QTY_FIELD: quantity},
        )

    def upsert_supplier_stock_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.create_supplier_stock_record(payload)
        except ODataSyncError as error:
            if "Запись с такими полями уже существует" not in str(error):
                raise

        supplier_nomenclature_key = str(payload.get(SUPPLIER_STOCK_OWNER_FIELD, "")).strip()
        if not supplier_nomenclature_key:
            raise ODataSyncError("Cannot update supplier stock record without supplier nomenclature key.")
        quantity = payload.get(SUPPLIER_STOCK_QTY_FIELD)
        if not isinstance(quantity, int | float):
            raise ODataSyncError(f"Cannot update supplier stock record with invalid quantity: {quantity!r}")
        return self.update_supplier_stock_record(supplier_nomenclature_key, quantity)
