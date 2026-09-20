import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from nintendo_stock_monitor.config import ConfigError, ProductConfig
from nintendo_stock_monitor.models import AlertKind, CheckResult


class NotificationError(RuntimeError):
    """Raised when GitHub does not accept a notification Issue."""


@dataclass(frozen=True, slots=True)
class GitHubSettings:
    token: str
    repository: str
    assignee: str
    api_url: str = "https://api.github.com"

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "GitHubSettings":
        values = environment if environment is not None else os.environ
        token = values.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise ConfigError("GITHUB_TOKEN is required")

        repository = values.get("GITHUB_REPOSITORY", "").strip()
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise ConfigError("GITHUB_REPOSITORY must use owner/repository format")

        assignee = values.get("GITHUB_ASSIGNEE", "").strip()
        if not assignee:
            assignee = values.get("GITHUB_REPOSITORY_OWNER", "").strip()
        if not assignee:
            raise ConfigError("GITHUB_ASSIGNEE or GITHUB_REPOSITORY_OWNER is required")

        configured_api_url = values.get("GITHUB_API_URL", "").strip()
        api_url = (configured_api_url or "https://api.github.com").rstrip("/")
        parsed_api_url = urlparse(api_url)
        if parsed_api_url.scheme != "https" or not parsed_api_url.hostname:
            raise ConfigError("GITHUB_API_URL must be an absolute HTTPS URL")

        return cls(
            token=token,
            repository=repository,
            assignee=assignee,
            api_url=api_url,
        )


@dataclass(frozen=True, slots=True)
class Notification:
    title: str
    body: str


def build_notification(
    alert_kind: AlertKind,
    product: ProductConfig,
    result: CheckResult,
) -> Notification:
    checked_at = result.checked_at.isoformat().replace("+00:00", "Z")
    product_link = f"[{product.name}]({product.url})"
    if alert_kind is AlertKind.AVAILABLE:
        return Notification(
            title=f"Nintendo stock confirmed: {product.sku}",
            body=(
                "## Confirmed availability\n\n"
                f"The monitor found purchase evidence for {product_link}.\n\n"
                f"- SKU: `{product.sku}`\n"
                f"- Signal: `{result.reason.value}`\n"
                f"- Checked: `{checked_at}`\n\n"
                f"[Open the Nintendo Store]({product.url})"
            ),
        )

    event_line = f"- Queue event: `{result.queue_event_id}`\n" if result.queue_event_id else ""
    return Notification(
        title=f"Nintendo waiting room active: {product.sku}",
        body=(
            "## Manual check recommended\n\n"
            f"Nintendo activated a waiting room for {product_link}. "
            "Stock is **not confirmed**.\n\n"
            f"- SKU: `{product.sku}`\n"
            f"{event_line}"
            f"- Checked: `{checked_at}`\n\n"
            f"[Open the Nintendo Store]({product.url})"
        ),
    )


def build_test_notification(product: ProductConfig) -> Notification:
    return Notification(
        title="Test: Nintendo stock monitor",
        body=(
            "GitHub Issue notifications are configured correctly. "
            "This is only a test.\n\n"
            f"[Open the configured product]({product.url})"
        ),
    )


def publish_notification(
    settings: GitHubSettings,
    notification: Notification,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout_seconds)
    owner, repository = settings.repository.split("/", maxsplit=1)
    endpoint = (
        f"{settings.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/issues"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {settings.token}",
        "User-Agent": "NintendoStockMonitor/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "title": notification.title,
        "body": notification.body,
        "assignees": [settings.assignee],
    }

    try:
        response = http_client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        suffix = f" (HTTP {status})" if status is not None else ""
        raise NotificationError(f"GitHub Issue notification failed{suffix}") from exc
    finally:
        if owns_client:
            http_client.close()
