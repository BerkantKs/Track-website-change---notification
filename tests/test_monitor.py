from pathlib import Path

import httpx
import pytest

from nintendo_stock_monitor.config import MonitorConfig, ProductConfig
from nintendo_stock_monitor.models import AlertKind, Availability
from nintendo_stock_monitor.monitor import run_check
from nintendo_stock_monitor.notifier import GitHubSettings, NotificationError
from nintendo_stock_monitor.state import StateError, load_state

PRODUCT_URL = "https://store.nintendo.com/fr-be/product-P00211"
AVAILABLE_HTML = """
<script type="application/ld+json">
{"@type":"Product","offers":{"availability":"https://schema.org/InStock"}}
</script>
"""
GITHUB_SETTINGS = GitHubSettings("token", "owner/repository", "owner")


def config() -> MonitorConfig:
    return MonitorConfig(
        product=ProductConfig("Zelda console", "P00211", PRODUCT_URL),
        timeout_seconds=1.0,
        retries=0,
        user_agent="NintendoStockMonitor/Test",
    )


def product_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=AVAILABLE_HTML, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_successful_alert_is_persisted_and_not_repeated(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    notifications: list[httpx.Request] = []

    def notification_handler(request: httpx.Request) -> httpx.Response:
        notifications.append(request)
        return httpx.Response(201, request=request)

    with (
        product_client() as product_http,
        httpx.Client(transport=httpx.MockTransport(notification_handler)) as notification_http,
    ):
        first = run_check(
            config(),
            state_path,
            github_settings=GITHUB_SETTINGS,
            product_client=product_http,
            notification_client=notification_http,
        )
        second = run_check(
            config(),
            state_path,
            github_settings=GITHUB_SETTINGS,
            product_client=product_http,
            notification_client=notification_http,
        )

    assert first.alert_kind is AlertKind.AVAILABLE
    assert first.notification_sent is True
    assert second.alert_kind is None
    assert second.notification_sent is False
    assert len(notifications) == 1
    assert load_state(state_path).last_confirmed is Availability.AVAILABLE


def test_failed_notification_does_not_advance_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    def notification_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    with (
        product_client() as product_http,
        httpx.Client(transport=httpx.MockTransport(notification_handler)) as notification_http,
        pytest.raises(NotificationError, match="HTTP 503"),
    ):
        run_check(
            config(),
            state_path,
            github_settings=GITHUB_SETTINGS,
            product_client=product_http,
            notification_client=notification_http,
        )

    assert not state_path.exists()


def test_successful_notification_followed_by_save_failure_is_at_least_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    notifications: list[httpx.Request] = []

    def notification_handler(request: httpx.Request) -> httpx.Response:
        notifications.append(request)
        return httpx.Response(201, request=request)

    def fail_save(*args, **kwargs) -> bool:
        raise StateError("simulated save failure")

    monkeypatch.setattr("nintendo_stock_monitor.monitor.save_state", fail_save)

    with (
        product_client() as product_http,
        httpx.Client(transport=httpx.MockTransport(notification_handler)) as notification_http,
        pytest.raises(StateError, match="simulated save failure"),
    ):
        run_check(
            config(),
            state_path,
            github_settings=GITHUB_SETTINGS,
            product_client=product_http,
            notification_client=notification_http,
        )

    assert len(notifications) == 1
    assert not state_path.exists()


def test_dry_run_neither_notifies_nor_writes_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"

    with product_client() as product_http:
        outcome = run_check(
            config(),
            state_path,
            github_settings=None,
            dry_run=True,
            product_client=product_http,
        )

    assert outcome.alert_kind is AlertKind.AVAILABLE
    assert outcome.notification_sent is False
    assert outcome.state_changed is False
    assert not state_path.exists()
