from dataclasses import dataclass
from pathlib import Path

import httpx

from nintendo_stock_monitor.checker import check_product
from nintendo_stock_monitor.config import MonitorConfig
from nintendo_stock_monitor.models import AlertKind, CheckResult
from nintendo_stock_monitor.notifier import (
    GitHubSettings,
    build_notification,
    publish_notification,
)
from nintendo_stock_monitor.state import (
    decide_transition,
    load_state,
    mark_alert_sent,
    save_state,
)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    result: CheckResult
    alert_kind: AlertKind | None
    notification_sent: bool
    state_changed: bool
    dry_run: bool


def run_check(
    config: MonitorConfig,
    state_path: Path,
    *,
    github_settings: GitHubSettings | None,
    dry_run: bool = False,
    product_client: httpx.Client | None = None,
    notification_client: httpx.Client | None = None,
) -> RunOutcome:
    current_state = load_state(state_path)
    result = check_product(
        config.product.url,
        client=product_client,
        timeout_seconds=config.timeout_seconds,
        retries=config.retries,
        expected_sku=config.product.sku,
        user_agent=config.user_agent,
    )
    decision = decide_transition(current_state, result)

    if dry_run:
        return RunOutcome(result, decision.alert_kind, False, False, True)

    next_state = decision.next_state
    notification_sent = False
    if decision.alert_kind is not None:
        if github_settings is None:
            raise ValueError("github_settings is required when an alert must be sent")
        notification = build_notification(decision.alert_kind, config.product, result)
        publish_notification(
            github_settings,
            notification,
            client=notification_client,
        )
        next_state = mark_alert_sent(
            next_state,
            decision.alert_kind,
            sent_at=result.checked_at,
        )
        notification_sent = True

    state_changed = save_state(state_path, next_state)
    return RunOutcome(
        result,
        decision.alert_kind,
        notification_sent,
        state_changed,
        False,
    )
