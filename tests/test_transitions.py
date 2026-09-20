from datetime import UTC, datetime

from nintendo_stock_monitor.models import (
    AlertKind,
    Availability,
    CheckResult,
    MonitorState,
    Reason,
)
from nintendo_stock_monitor.state import decide_transition, mark_alert_sent

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def result(
    availability: Availability,
    reason: Reason,
    *,
    queue_event_id: str | None = None,
) -> CheckResult:
    return CheckResult(
        availability=availability,
        reason=reason,
        checked_at=NOW,
        queue_event_id=queue_event_id,
    )


def test_available_alerts_once_until_two_unavailable_checks_rearm() -> None:
    first_available = decide_transition(
        MonitorState(),
        result(Availability.AVAILABLE, Reason.JSON_LD_IN_STOCK),
    )
    assert first_available.alert_kind is AlertKind.AVAILABLE

    notified_state = mark_alert_sent(
        first_available.next_state,
        AlertKind.AVAILABLE,
        sent_at=NOW,
    )
    repeated = decide_transition(
        notified_state,
        result(Availability.AVAILABLE, Reason.ENABLED_PURCHASE_CONTROL),
    )
    assert repeated.alert_kind is None

    unavailable_once = decide_transition(
        repeated.next_state,
        result(Availability.UNAVAILABLE, Reason.JSON_LD_OUT_OF_STOCK),
    )
    assert unavailable_once.next_state.last_confirmed is Availability.AVAILABLE
    assert unavailable_once.next_state.unavailable_streak == 1

    unavailable_twice = decide_transition(
        unavailable_once.next_state,
        result(Availability.UNAVAILABLE, Reason.DISABLED_PURCHASE_CONTROL),
    )
    assert unavailable_twice.next_state.last_confirmed is Availability.UNAVAILABLE

    restocked = decide_transition(
        unavailable_twice.next_state,
        result(Availability.AVAILABLE, Reason.JSON_LD_IN_STOCK),
    )
    assert restocked.alert_kind is AlertKind.AVAILABLE


def test_unknown_preserves_confirmed_state_and_breaks_unavailable_streak() -> None:
    state = MonitorState(
        last_confirmed=Availability.AVAILABLE,
        unavailable_streak=1,
    )

    decision = decide_transition(
        state,
        result(Availability.UNKNOWN, Reason.AMBIGUOUS_PAGE),
    )

    assert decision.alert_kind is None
    assert decision.next_state.last_confirmed is Availability.AVAILABLE
    assert decision.next_state.unavailable_streak == 0


def test_transient_unknown_preserves_queue_deduplication() -> None:
    state = MonitorState(queue_active=True, queue_event_id="lrwrv2")

    interrupted = decide_transition(
        state,
        result(Availability.UNKNOWN, Reason.SERVER_ERROR),
    )
    repeated_queue = decide_transition(
        interrupted.next_state,
        result(Availability.QUEUE, Reason.QUEUE_REDIRECT, queue_event_id="lrwrv2"),
    )

    assert interrupted.next_state.queue_active is True
    assert interrupted.next_state.queue_event_id == "lrwrv2"
    assert repeated_queue.alert_kind is None


def test_same_queue_event_does_not_realert_after_direct_page() -> None:
    state = MonitorState(queue_active=True, queue_event_id="lrwrv2")
    direct_page = decide_transition(
        state,
        result(Availability.UNKNOWN, Reason.AMBIGUOUS_PAGE),
    )
    repeated_queue = decide_transition(
        direct_page.next_state,
        result(Availability.QUEUE, Reason.QUEUE_REDIRECT, queue_event_id="lrwrv2"),
    )

    assert direct_page.next_state.queue_active is False
    assert direct_page.next_state.queue_event_id == "lrwrv2"
    assert repeated_queue.alert_kind is None


def test_queue_alerts_once_and_alerts_again_for_a_new_event() -> None:
    first = decide_transition(
        MonitorState(),
        result(Availability.QUEUE, Reason.QUEUE_REDIRECT, queue_event_id="lrwrv2"),
    )
    assert first.alert_kind is AlertKind.QUEUE

    repeated = decide_transition(
        first.next_state,
        result(Availability.QUEUE, Reason.QUEUE_REDIRECT, queue_event_id="lrwrv2"),
    )
    assert repeated.alert_kind is None

    changed = decide_transition(
        repeated.next_state,
        result(Availability.QUEUE, Reason.QUEUE_REDIRECT, queue_event_id="zelda2"),
    )
    assert changed.alert_kind is AlertKind.QUEUE


def test_transition_updates_monthly_heartbeat() -> None:
    decision = decide_transition(
        MonitorState(heartbeat_month="2026-08"),
        result(Availability.UNKNOWN, Reason.TRANSPORT_ERROR),
        now=NOW,
    )

    assert decision.next_state.heartbeat_month == "2026-09"
