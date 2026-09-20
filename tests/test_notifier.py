from datetime import UTC, datetime

import httpx
import pytest

from nintendo_stock_monitor.config import ConfigError, ProductConfig
from nintendo_stock_monitor.models import AlertKind, Availability, CheckResult, Reason
from nintendo_stock_monitor.notifier import (
    NtfySettings,
    build_notification,
    publish_notification,
)


def test_authenticated_notification_contains_actionable_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, request=request)

    product = ProductConfig("Zelda console", "P00211", "https://example.com/product")
    result = CheckResult(
        Availability.AVAILABLE,
        Reason.JSON_LD_IN_STOCK,
        checked_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    notification = build_notification(AlertKind.AVAILABLE, product, result)
    settings = NtfySettings(topic="private-stock-topic", token="secret-token")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publish_notification(settings, notification, client=client)

    request = captured[0]
    assert str(request.url) == "https://ntfy.sh/private-stock-topic"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["Priority"] == "5"
    assert request.headers["Click"] == product.url
    assert b"Purchase evidence is live" in request.content


def test_empty_optional_server_uses_ntfy_default() -> None:
    settings = NtfySettings.from_environment(
        {"NTFY_TOPIC": "private-stock-topic", "NTFY_SERVER_URL": ""}
    )

    assert settings.server_url == "https://ntfy.sh"


def test_queue_notification_is_explicitly_unconfirmed() -> None:
    product = ProductConfig("Zelda console", "P00211", "https://example.com/product")
    result = CheckResult(
        Availability.QUEUE,
        Reason.QUEUE_REDIRECT,
        checked_at=datetime(2026, 9, 20, tzinfo=UTC),
        queue_event_id="lrwrv2",
    )

    notification = build_notification(AlertKind.QUEUE, product, result)

    assert notification.priority == 3
    assert "Stock is not confirmed" in notification.message
    assert "lrwrv2" in notification.message


@pytest.mark.parametrize("topic", ["", "contains/slash", "has?query", "has#fragment"])
def test_invalid_topic_is_rejected(topic: str) -> None:
    with pytest.raises(ConfigError):
        NtfySettings.from_environment({"NTFY_TOPIC": topic})
