import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigError(RuntimeError):
    """Raised when project or environment configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ProductConfig:
    name: str
    sku: str
    url: str


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    product: ProductConfig
    timeout_seconds: float
    retries: int
    user_agent: str


def _table(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing or invalid [{key}] table")
    return value


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string")
    return value.strip()


def load_config(path: Path) -> MonitorConfig:
    try:
        with path.open("rb") as config_file:
            payload = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc

    product_payload = _table(payload, "product")
    monitor_payload = _table(payload, "monitor")
    product_url = _string(product_payload, "url")
    parsed_url = urlparse(product_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise ConfigError("product.url must be an absolute HTTPS URL")

    timeout_seconds = monitor_payload.get("timeout_seconds")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
        or timeout_seconds > 60
    ):
        raise ConfigError("monitor.timeout_seconds must be greater than 0 and at most 60")

    retries = monitor_payload.get("retries")
    if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= 3:
        raise ConfigError("monitor.retries must be an integer between 0 and 3")

    return MonitorConfig(
        product=ProductConfig(
            name=_string(product_payload, "name"),
            sku=_string(product_payload, "sku"),
            url=product_url,
        ),
        timeout_seconds=float(timeout_seconds),
        retries=retries,
        user_agent=_string(monitor_payload, "user_agent"),
    )
