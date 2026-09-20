from pathlib import Path

import httpx
import pytest

from nintendo_stock_monitor.checker import check_product, classify_html
from nintendo_stock_monitor.models import Availability, Reason

PRODUCT_URL = (
    "https://store.nintendo.com/fr-be/"
    "nintendo-switch-2-edition-40e-anniversaire-de-the-legend-of-zelda-P00211"
)
FIXTURES = Path(__file__).parent / "fixtures"


def test_queue_redirect_is_not_reported_as_available() -> None:
    queue_url = f"https://nintendostoreuk.queue-it.net/?c=nintendostoreuk&e=lrwrv2&t={PRODUCT_URL}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": queue_url}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = check_product(PRODUCT_URL, client=client)

    assert result.availability is Availability.QUEUE
    assert result.reason is Reason.QUEUE_REDIRECT
    assert result.queue_event_id == "lrwrv2"
    assert result.availability is not Availability.AVAILABLE


def test_queue_redirect_without_event_id_is_still_queue() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://nintendostoreuk.queue-it.net/"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client)

    assert result.availability is Availability.QUEUE
    assert result.queue_event_id is None


@pytest.mark.parametrize(
    ("fixture_name", "availability", "reason"),
    [
        ("in_stock.html", Availability.AVAILABLE, Reason.JSON_LD_IN_STOCK),
        ("out_of_stock.html", Availability.UNAVAILABLE, Reason.JSON_LD_OUT_OF_STOCK),
        (
            "available_button.html",
            Availability.AVAILABLE,
            Reason.ENABLED_PURCHASE_CONTROL,
        ),
        (
            "disabled_button.html",
            Availability.UNAVAILABLE,
            Reason.DISABLED_PURCHASE_CONTROL,
        ),
        ("ambiguous.html", Availability.UNKNOWN, Reason.AMBIGUOUS_PAGE),
    ],
)
def test_html_classification(
    fixture_name: str,
    availability: Availability,
    reason: Reason,
) -> None:
    html = (FIXTURES / fixture_name).read_text(encoding="utf-8")

    result = classify_html(html, expected_sku="P00211")

    assert result.availability is availability
    assert result.reason is reason


@pytest.mark.parametrize("status_code", [404, 410])
def test_removed_product_is_unavailable(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client)

    assert result.availability is Availability.UNAVAILABLE
    assert result.reason is Reason.NOT_FOUND


def test_same_origin_redirect_is_followed_with_a_bound() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(301, headers={"Location": "/fr-be/canonical"}, request=request)
        html = (FIXTURES / "in_stock.html").read_text(encoding="utf-8")
        return httpx.Response(200, text=html, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client)

    assert result.availability is Availability.AVAILABLE
    assert str(requests[-1].url) == "https://store.nintendo.com/fr-be/canonical"


def test_cross_origin_redirect_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"Location": "https://example.com/not-the-store"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client)

    assert result.availability is Availability.UNKNOWN
    assert result.reason is Reason.UNEXPECTED_REDIRECT
    assert result.detail == "cross_origin"


def test_same_origin_redirect_limit_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/loop"}, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client, max_redirects=1)

    assert result.availability is Availability.UNKNOWN
    assert result.reason is Reason.UNEXPECTED_REDIRECT
    assert result.detail == "redirect_limit"


def test_transient_server_error_is_retried_once() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, text="<html><body>Product page</body></html>", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client, sleeper=delays.append)

    assert attempts == 2
    assert delays == [0.0]
    assert result.availability is Availability.UNKNOWN
    assert result.reason is Reason.AMBIGUOUS_PAGE


def test_transport_failure_is_unknown_after_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_product(PRODUCT_URL, client=client, sleeper=lambda _: None)

    assert attempts == 2
    assert result.availability is Availability.UNKNOWN
    assert result.reason is Reason.TRANSPORT_ERROR


def test_enabled_purchase_control_wins_over_disabled_duplicate() -> None:
    html = """
        <button disabled>Ajouter au panier</button>
        <button>Ajouter au panier</button>
        """

    result = classify_html(html)

    assert result.availability is Availability.AVAILABLE
    assert result.reason is Reason.ENABLED_PURCHASE_CONTROL


def test_input_button_is_a_purchase_control() -> None:
    result = classify_html('<input type="button" value="Ajouter au panier">')

    assert result.availability is Availability.AVAILABLE


def test_unrelated_product_json_ld_does_not_confirm_target_stock() -> None:
    html = """
        <script type="application/ld+json">
            {"@type":"Product","sku":"OTHER-1","offers":{"availability":"InStock"}}
        </script>
        <script type="application/ld+json">
            {"@type":"Product","sku":"OTHER-2","offers":{"availability":"OutOfStock"}}
        </script>
        """

    result = classify_html(html, expected_sku="P00211")

    assert result.availability is Availability.UNKNOWN
    assert result.reason is Reason.AMBIGUOUS_PAGE


def test_purchase_control_is_scoped_to_target_sku() -> None:
    html = """
        <div data-sku="OTHER-1"><button>Ajouter au panier</button></div>
        <div data-sku="P00211"><button disabled>Ajouter au panier</button></div>
        """

    result = classify_html(html, expected_sku="P00211")

    assert result.availability is Availability.UNAVAILABLE
    assert result.reason is Reason.DISABLED_PURCHASE_CONTROL
