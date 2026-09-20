import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from nintendo_stock_monitor.config import ConfigError, ProductConfig
from nintendo_stock_monitor.models import AlertKind, CheckResult


class NtfyError(RuntimeError):
    """Raised when ntfy does not accept a notification."""


@dataclass(frozen=True, slots=True)
class NtfySettings:
    topic: str
    token: str | None = None
    server_url: str = "https://ntfy.sh"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "NtfySettings":
        values = environment if environment is not None else os.environ
        topic = values.get("NTFY_TOPIC", "").strip()
        if not topic:
            raise ConfigError("NTFY_TOPIC is required")
        if any(character in topic for character in "/?#"):
            raise ConfigError("NTFY_TOPIC must be a single topic name without /, ?, or #")

        configured_server = values.get("NTFY_SERVER_URL", "").strip()
        server_url = (configured_server or "https://ntfy.sh").rstrip("/")
        parsed_server = urlparse(server_url)
        if parsed_server.scheme not in {"http", "https"} or not parsed_server.hostname:
            raise ConfigError("NTFY_SERVER_URL must be an absolute HTTP(S) URL")

        token = values.get("NTFY_TOKEN", "").strip() or None
        return cls(topic=topic, token=token, server_url=server_url)


@dataclass(frozen=True, slots=True)
class Notification:
    title: str
    message: str
    priority: int
    tags: tuple[str, ...]
    click_url: str


def build_notification(
    alert_kind: AlertKind,
    product: ProductConfig,
    result: CheckResult,
) -> Notification:
    if alert_kind is AlertKind.AVAILABLE:
        return Notification(
            title="Nintendo stock confirmed",
            message=(
                f"Purchase evidence is live for {product.name} ({product.sku}). "
                "Open the Nintendo Store now."
            ),
            priority=5,
            tags=("rotating_light", "video_game"),
            click_url=product.url,
        )

    event_detail = f" Queue event: {result.queue_event_id}." if result.queue_event_id else ""
    return Notification(
        title="Nintendo waiting room active",
        message=(
            f"Nintendo activated a waiting room for {product.name} ({product.sku}). "
            f"Stock is not confirmed; check the store manually.{event_detail}"
        ),
        priority=3,
        tags=("hourglass_flowing_sand", "video_game"),
        click_url=product.url,
    )


def build_test_notification(product: ProductConfig) -> Notification:
    return Notification(
        title="Nintendo monitor test",
        message="ntfy delivery is configured correctly. This is only a test.",
        priority=3,
        tags=("white_check_mark", "video_game"),
        click_url=product.url,
    )


def publish_notification(
    settings: NtfySettings,
    notification: Notification,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout_seconds)
    headers = {
        "Title": notification.title,
        "Priority": str(notification.priority),
        "Tags": ",".join(notification.tags),
        "Click": notification.click_url,
    }
    if settings.token:
        headers["Authorization"] = f"Bearer {settings.token}"

    endpoint = f"{settings.server_url}/{quote(settings.topic, safe='')}"
    try:
        response = http_client.post(endpoint, content=notification.message, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        suffix = f" (HTTP {status})" if status is not None else ""
        raise NtfyError(f"ntfy notification failed{suffix}") from exc
    finally:
        if owns_client:
            http_client.close()
