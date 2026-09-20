import json
from datetime import UTC, datetime

import httpx
import pytest

from nintendo_stock_monitor.config import ConfigError, ProductConfig
from nintendo_stock_monitor.models import AlertKind, Availability, CheckResult, Reason
from nintendo_stock_monitor.notifier import (
    GitHubSettings,
    build_notification,
    publish_notification,
)


def test_github_issue_is_assigned_and_contains_product_link() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, request=request)

    product = ProductConfig("Zelda console", "P00211", "https://example.com/product")
    result = CheckResult(
        Availability.AVAILABLE,
        Reason.JSON_LD_IN_STOCK,
        checked_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    notification = build_notification(AlertKind.AVAILABLE, product, result)
    settings = GitHubSettings(
        token="secret-token",
        repository="BerkantKs/stock-monitor",
        assignee="BerkantKs",
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publish_notification(settings, notification, client=client)

    request = captured[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://api.github.com/repos/BerkantKs/stock-monitor/issues"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert payload["assignees"] == ["BerkantKs"]
    assert payload["title"] == "Nintendo stock confirmed: P00211"
    assert product.url in payload["body"]


def test_actions_environment_uses_repository_owner_as_assignee() -> None:
    settings = GitHubSettings.from_environment(
        {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "BerkantKs/stock-monitor",
            "GITHUB_REPOSITORY_OWNER": "BerkantKs",
            "GITHUB_API_URL": "",
        }
    )

    assert settings.assignee == "BerkantKs"
    assert settings.api_url == "https://api.github.com"


def test_queue_notification_is_explicitly_unconfirmed() -> None:
    product = ProductConfig("Zelda console", "P00211", "https://example.com/product")
    result = CheckResult(
        Availability.QUEUE,
        Reason.QUEUE_REDIRECT,
        checked_at=datetime(2026, 9, 20, tzinfo=UTC),
        queue_event_id="lrwrv2",
    )

    notification = build_notification(AlertKind.QUEUE, product, result)

    assert "Stock is **not confirmed**" in notification.body
    assert "lrwrv2" in notification.body


@pytest.mark.parametrize(
    "environment",
    [
        {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_REPOSITORY_OWNER": "owner"},
        {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY_OWNER": "owner"},
        {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "invalid",
            "GITHUB_REPOSITORY_OWNER": "owner",
        },
        {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": "owner/repo"},
    ],
)
def test_invalid_github_environment_is_rejected(environment: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        GitHubSettings.from_environment(environment)
