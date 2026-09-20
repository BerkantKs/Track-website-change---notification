import json
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from nintendo_stock_monitor.models import Availability, CheckResult, Reason

_QUEUE_HOST_SUFFIXES = ("queue-it.net", "queue-it.com")
_IN_STOCK_VALUES = {"instock", "limitedavailability", "preorder", "presale"}
_OUT_OF_STOCK_VALUES = {
    "discontinued",
    "outofstock",
    "preorderclosed",
    "soldout",
}
_PURCHASE_TEXT = ("ajouter au panier", "add to cart", "précommander", "pre-order")
_SOLD_OUT_TEXT = (
    "actuellement indisponible",
    "indisponible",
    "rupture de stock",
    "épuisé",
    "sold out",
    "out of stock",
)
_PRODUCT_IDENTIFIER_ATTRIBUTES = (
    "data-pid",
    "data-product-id",
    "data-product-sku",
    "data-sku",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_queue_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(
        hostname == suffix or hostname.endswith(f".{suffix}") for suffix in _QUEUE_HOST_SUFFIXES
    )


def _queue_event_id(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get("e", [])
    return values[0] if values else None


def _same_origin(first_url: str, second_url: str) -> bool:
    first = urlparse(first_url)
    second = urlparse(second_url)
    return (
        first.scheme.lower() == second.scheme.lower()
        and (first.hostname or "").lower() == (second.hostname or "").lower()
        and first.port == second.port
    )


def _iter_json_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_objects(child)


def _availability_token(value: object) -> str:
    return str(value).rsplit("/", maxsplit=1)[-1].replace("_", "").lower()


def _schema_types(item: dict[str, Any]) -> set[str]:
    raw_types = item.get("@type", [])
    values = raw_types if isinstance(raw_types, list) else [raw_types]
    return {str(value).rsplit("/", maxsplit=1)[-1].casefold() for value in values}


def _normalized_identifier(value: object) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _product_identifiers(product: dict[str, Any]) -> set[str]:
    identifiers: set[str] = set()
    for key in ("sku", "productID", "mpn"):
        value = product.get(key)
        if value is not None:
            identifiers.add(_normalized_identifier(value))
    return identifiers


def _product_availability(product: dict[str, Any]) -> Availability | None:
    tokens: set[str] = set()
    for item in _iter_json_objects(product):
        if "availability" in item:
            tokens.add(_availability_token(item["availability"]))

    has_available = bool(tokens & _IN_STOCK_VALUES)
    has_unavailable = bool(tokens & _OUT_OF_STOCK_VALUES)
    if has_available == has_unavailable:
        return None
    return Availability.AVAILABLE if has_available else Availability.UNAVAILABLE


def _json_ld_availability(
    soup: BeautifulSoup,
    expected_sku: str | None,
) -> Availability | None:
    products: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _iter_json_objects(payload):
            if "product" in _schema_types(item):
                products.append(item)

    candidates = products
    if expected_sku is not None:
        normalized_sku = _normalized_identifier(expected_sku)
        matching_products = [
            product
            for product in products
            if any(normalized_sku in value for value in _product_identifiers(product))
        ]
        if matching_products:
            candidates = matching_products
        elif len(products) == 1 and not _product_identifiers(products[0]):
            candidates = products
        else:
            return None
    elif len(products) != 1:
        return None

    classifications = {
        availability
        for product in candidates
        if (availability := _product_availability(product)) is not None
    }
    if len(classifications) != 1:
        return None
    return classifications.pop()


def _is_disabled(control: Tag) -> bool:
    classes = {str(value).lower() for value in control.get("class", [])}
    return (
        control.has_attr("disabled")
        or control.get("aria-disabled", "").lower() == "true"
        or "disabled" in classes
        or "is-disabled" in classes
    )


def _control_identifiers(control: Tag) -> set[str]:
    identifiers: set[str] = set()
    current: Tag | None = control
    for _ in range(5):
        if current is None:
            break
        for attribute in _PRODUCT_IDENTIFIER_ATTRIBUTES:
            value = current.get(attribute)
            if isinstance(value, str):
                identifiers.add(_normalized_identifier(value))
        current = current.parent if isinstance(current.parent, Tag) else None
    return identifiers


def _purchase_control(
    soup: BeautifulSoup,
    expected_sku: str | None,
) -> Availability | None:
    controls: list[Tag] = []
    selector = "button, input[type=button], input[type=submit], a[role=button]"
    for control in soup.select(selector):
        text = " ".join(
            value
            for value in (
                control.get_text(" ", strip=True),
                control.get("value"),
                control.get("aria-label"),
            )
            if isinstance(value, str)
        ).casefold()
        if any(phrase in text for phrase in _PURCHASE_TEXT):
            controls.append(control)

    if not controls:
        return None
    if expected_sku is not None:
        normalized_sku = _normalized_identifier(expected_sku)
        identified_controls = [control for control in controls if _control_identifiers(control)]
        matching_controls = [
            control
            for control in identified_controls
            if any(normalized_sku in value for value in _control_identifiers(control))
        ]
        if matching_controls:
            controls = matching_controls
        elif identified_controls or len(controls) != 1:
            return None

    if any(not _is_disabled(control) for control in controls):
        return Availability.AVAILABLE
    return Availability.UNAVAILABLE


def classify_html(
    html: str,
    *,
    expected_sku: str | None = None,
    checked_at: datetime | None = None,
) -> CheckResult:
    soup = BeautifulSoup(html, "html.parser")
    json_ld = _json_ld_availability(soup, expected_sku)
    if json_ld is Availability.AVAILABLE:
        return CheckResult(json_ld, Reason.JSON_LD_IN_STOCK, checked_at or _now())
    if json_ld is Availability.UNAVAILABLE:
        return CheckResult(json_ld, Reason.JSON_LD_OUT_OF_STOCK, checked_at or _now())

    control = _purchase_control(soup, expected_sku)
    if control is Availability.AVAILABLE:
        return CheckResult(control, Reason.ENABLED_PURCHASE_CONTROL, checked_at or _now())
    if control is Availability.UNAVAILABLE:
        return CheckResult(control, Reason.DISABLED_PURCHASE_CONTROL, checked_at or _now())

    page_text = soup.get_text(" ", strip=True).casefold()
    if any(phrase in page_text for phrase in _SOLD_OUT_TEXT):
        return CheckResult(Availability.UNAVAILABLE, Reason.SOLD_OUT_TEXT, checked_at or _now())
    return CheckResult(Availability.UNKNOWN, Reason.AMBIGUOUS_PAGE, checked_at or _now())


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
    return float(2**attempt)


def check_product(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
    retries: int = 1,
    max_redirects: int = 3,
    expected_sku: str | None = None,
    user_agent: str = "NintendoStockMonitor/0.1 (personal availability checker)",
    sleeper: Callable[[float], None] = time.sleep,
) -> CheckResult:
    if max_redirects < 0:
        raise ValueError("max_redirects cannot be negative")
    if retries < 0:
        raise ValueError("retries cannot be negative")
    owns_client = client is None
    http_client = client or httpx.Client(
        follow_redirects=False,
        timeout=timeout_seconds,
    )
    try:
        for attempt in range(retries + 1):
            response: httpx.Response | None = None
            try:
                current_url = url
                for redirect_count in range(max_redirects + 1):
                    response = http_client.get(
                        current_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml",
                            "User-Agent": user_agent,
                        },
                        follow_redirects=False,
                    )
                    location = response.headers.get("Location")
                    if not response.is_redirect or not location:
                        break

                    redirect_url = urljoin(str(response.url), location)
                    if _is_queue_url(redirect_url):
                        return CheckResult(
                            Availability.QUEUE,
                            Reason.QUEUE_REDIRECT,
                            _now(),
                            status_code=response.status_code,
                            final_url=redirect_url,
                            queue_event_id=_queue_event_id(redirect_url),
                        )
                    if not _same_origin(url, redirect_url):
                        return CheckResult(
                            Availability.UNKNOWN,
                            Reason.UNEXPECTED_REDIRECT,
                            _now(),
                            status_code=response.status_code,
                            final_url=redirect_url,
                            detail="cross_origin",
                        )
                    if redirect_count >= max_redirects:
                        return CheckResult(
                            Availability.UNKNOWN,
                            Reason.UNEXPECTED_REDIRECT,
                            _now(),
                            status_code=response.status_code,
                            final_url=redirect_url,
                            detail="redirect_limit",
                        )
                    current_url = redirect_url
            except httpx.TransportError as exc:
                if attempt < retries:
                    sleeper(_retry_delay(None, attempt))
                    continue
                return CheckResult(
                    Availability.UNKNOWN,
                    Reason.TRANSPORT_ERROR,
                    _now(),
                    final_url=url,
                    detail=type(exc).__name__,
                )

            if response is None:
                raise AssertionError("request loop did not produce a response")

            if response.status_code in {404, 410}:
                return CheckResult(
                    Availability.UNAVAILABLE,
                    Reason.NOT_FOUND,
                    _now(),
                    status_code=response.status_code,
                    final_url=str(response.url),
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < retries:
                    sleeper(_retry_delay(response, attempt))
                    continue
                reason = Reason.RATE_LIMITED if response.status_code == 429 else Reason.SERVER_ERROR
                return CheckResult(
                    Availability.UNKNOWN,
                    reason,
                    _now(),
                    status_code=response.status_code,
                    final_url=str(response.url),
                )
            if response.status_code != 200:
                return CheckResult(
                    Availability.UNKNOWN,
                    Reason.UNEXPECTED_STATUS,
                    _now(),
                    status_code=response.status_code,
                    final_url=str(response.url),
                )

            parsed = classify_html(response.text, expected_sku=expected_sku)
            return CheckResult(
                parsed.availability,
                parsed.reason,
                parsed.checked_at,
                status_code=response.status_code,
                final_url=str(response.url),
                detail=parsed.detail,
            )
        raise AssertionError("retry loop exited unexpectedly")
    finally:
        if owns_client:
            http_client.close()
