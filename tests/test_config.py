from pathlib import Path

import pytest

from nintendo_stock_monitor.config import ConfigError, load_config


def test_project_config_loads_target_product() -> None:
    config = load_config(Path("config.toml"))

    assert config.product.sku == "P00211"
    assert config.product.url.startswith("https://store.nintendo.com/fr-be/")
    assert config.retries == 1


def test_non_https_product_url_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[product]
name = "Example"
sku = "P1"
url = "http://example.com/product"

[monitor]
timeout_seconds = 10
retries = 1
user_agent = "Test"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="HTTPS"):
        load_config(config_path)
