from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


DEFAULT_API_BASE_URL = "https://oapi.3logic.ru"
DEFAULT_TOKEN_CACHE = ".3logic_tokens.json"


class ThreeLogicApiError(RuntimeError):
    """Raised when the 3Logic API returns an unsuccessful response."""

    def __init__(self, message: str, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True)
class ThreeLogicSettings:
    api_base_url: str
    login: str
    password: str
    token_cache_path: Path

    @classmethod
    def from_env(cls) -> "ThreeLogicSettings":
        load_dotenv()

        login = os.getenv("THREELOGIC_LOGIN", "").strip()
        password = os.getenv("THREELOGIC_PASSWORD", "").strip()

        if not login or not password:
            raise ValueError(
                "Set THREELOGIC_LOGIN and THREELOGIC_PASSWORD in .env before calling the API."
            )

        return cls(
            api_base_url=os.getenv("THREELOGIC_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
            login=login,
            password=password,
            token_cache_path=Path(os.getenv("THREELOGIC_TOKEN_CACHE", DEFAULT_TOKEN_CACHE)),
        )


class ThreeLogicClient:
    def __init__(self, settings: ThreeLogicSettings | None = None, timeout: int = 30) -> None:
        self.settings = settings or ThreeLogicSettings.from_env()
        self.timeout = timeout
        self.session = requests.Session()
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    def login(self) -> dict[str, str]:
        payload = {
            "login": self.settings.login,
            "password": self.settings.password,
        }
        data = self._request("POST", "/auth/login", json=payload)

        self.access_token = self._require_token(data, "access_token")
        self.refresh_token = self._require_token(data, "refresh_token")
        self._save_tokens()
        return {"access_token": self.access_token, "refresh_token": self.refresh_token}

    def refresh_access_token(self) -> str:
        if not self.refresh_token:
            cached_tokens = self._load_tokens()
            self.refresh_token = cached_tokens.get("refresh_token")

        if not self.refresh_token:
            raise ThreeLogicApiError("Refresh token is missing. Call login() first.")

        data = self._request(
            "GET",
            "/auth/refresh",
            headers={"Authorization": f"Bearer {self.refresh_token}"},
        )

        self.access_token = self._require_token(data, "access_token")
        self._save_tokens()
        return self.access_token

    def authenticate(self) -> str:
        cached_tokens = self._load_tokens()
        self.access_token = cached_tokens.get("access_token")
        self.refresh_token = cached_tokens.get("refresh_token")

        if self.refresh_token:
            try:
                return self.refresh_access_token()
            except ThreeLogicApiError:
                # If refresh is rejected or expired, request a fresh token pair with login/password.
                pass

        tokens = self.login()
        return tokens["access_token"]

    def ping(self) -> dict[str, Any]:
        return self._request("GET", "/auth/ping", headers=self.auth_headers())

    def list_products(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/catalog/products",
            headers=self.auth_headers(),
            json=payload,
        )

    def list_product_categories(self) -> list[dict[str, Any]]:
        data = self._request_any(
            "GET",
            "/api/catalog/product-categories",
            headers=self.auth_headers(),
        )

        if isinstance(data, list):
            categories = data
        elif isinstance(data, dict) and isinstance(data.get("data"), list):
            categories = data["data"]
        else:
            raise ThreeLogicApiError("Product categories response contains invalid data.", payload=data)

        return [category for category in categories if isinstance(category, dict)]

    def iter_products(
        self,
        per_page: int = 200,
        only_in_stock: bool | None = None,
        return_on_order_products: bool | None = None,
        add_attributes: bool = True,
        add_description: bool = True,
        add_photos: bool = True,
        filters: dict[str, Any] | None = None,
    ):
        page = 1
        base_payload: dict[str, Any] = {
            "per_page": per_page,
            "add_attributes": add_attributes,
            "add_description": add_description,
            "add_photos": add_photos,
        }

        if only_in_stock is not None:
            base_payload["only_in_stock"] = only_in_stock
        if return_on_order_products is not None:
            base_payload["return_on_order_products"] = return_on_order_products
        if filters:
            base_payload.update(filters)

        while True:
            payload = {**base_payload, "page": page}
            response = self.list_products(payload)
            products = response.get("data", [])

            if not isinstance(products, list):
                raise ThreeLogicApiError("Products response contains invalid data field.", payload=response)

            yield from products

            total_pages = int(response.get("pages") or 0)
            if page >= total_pages or not products:
                break

            page += 1

    def auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            self.authenticate()
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        data = self._request_any(method, path, **kwargs)
        if not isinstance(data, dict):
            raise ThreeLogicApiError(f"Unexpected API response from {path}: {data!r}")

        return data

    def _request_any(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.settings.api_base_url}{path}"
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)

        try:
            data = response.json()
        except ValueError:
            data = response.text

        if response.status_code >= 400:
            message = self._extract_error_message(data)
            raise ThreeLogicApiError(message, response.status_code, data)

        return data

    def _load_tokens(self) -> dict[str, str]:
        if not self.settings.token_cache_path.exists():
            return {}

        try:
            data = json.loads(self.settings.token_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(data, dict):
            return {}

        return {
            "access_token": str(data.get("access_token", "")).strip(),
            "refresh_token": str(data.get("refresh_token", "")).strip(),
        }

    def _save_tokens(self) -> None:
        if not self.access_token and not self.refresh_token:
            return

        token_data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "saved_at": int(time.time()),
        }
        self.settings.token_cache_path.write_text(
            json.dumps(token_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _require_token(data: dict[str, Any], token_name: str) -> str:
        token = str(data.get(token_name, "")).strip()
        if not token:
            raise ThreeLogicApiError(f"API response does not contain {token_name}.", payload=data)
        return token

    @staticmethod
    def _extract_error_message(data: Any) -> str:
        if isinstance(data, dict):
            detail = data.get("detail")
            if isinstance(detail, list) and detail:
                return json.dumps(detail, ensure_ascii=False)
            if detail:
                return str(detail)

            for field_name in ("code", "message", "error"):
                value = data.get(field_name)
                if value:
                    return str(value)

            return json.dumps(data, ensure_ascii=False)

        return str(data)
