import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nintendo_stock_monitor.models import (
    AlertKind,
    Availability,
    CheckResult,
    MonitorState,
    Reason,
    TransitionDecision,
)

STATE_SCHEMA_VERSION = 1


class StateError(RuntimeError):
    """Raised when persisted monitor state cannot be read safely."""


def _heartbeat_month(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m")


def decide_transition(
    state: MonitorState,
    result: CheckResult,
    *,
    now: datetime | None = None,
) -> TransitionDecision:
    observed_at = now or result.checked_at
    heartbeat_month = _heartbeat_month(observed_at)

    if result.availability is Availability.AVAILABLE:
        alert_kind = (
            AlertKind.AVAILABLE if state.last_confirmed is not Availability.AVAILABLE else None
        )
        next_state = replace(
            state,
            last_confirmed=Availability.AVAILABLE,
            unavailable_streak=0,
            queue_active=False,
            heartbeat_month=heartbeat_month,
        )
        return TransitionDecision(alert_kind, next_state)

    if result.availability is Availability.UNAVAILABLE:
        unavailable_streak = min(state.unavailable_streak + 1, 2)
        last_confirmed = (
            Availability.UNAVAILABLE if unavailable_streak >= 2 else state.last_confirmed
        )
        next_state = replace(
            state,
            last_confirmed=last_confirmed,
            unavailable_streak=unavailable_streak,
            queue_active=False,
            heartbeat_month=heartbeat_month,
        )
        return TransitionDecision(None, next_state)

    if result.availability is Availability.QUEUE:
        if result.queue_event_id is not None:
            should_alert = result.queue_event_id != state.queue_event_id
        else:
            should_alert = not state.queue_active
        alert_kind = AlertKind.QUEUE if should_alert else None
        queue_event_id = result.queue_event_id
        if queue_event_id is None and state.queue_active:
            queue_event_id = state.queue_event_id
        next_state = replace(
            state,
            unavailable_streak=0,
            queue_active=True,
            queue_event_id=queue_event_id,
            heartbeat_month=heartbeat_month,
        )
        return TransitionDecision(alert_kind, next_state)

    preserve_queue = result.reason in {
        Reason.RATE_LIMITED,
        Reason.SERVER_ERROR,
        Reason.TRANSPORT_ERROR,
    }
    next_state = replace(
        state,
        unavailable_streak=0,
        queue_active=state.queue_active if preserve_queue else False,
        heartbeat_month=heartbeat_month,
    )
    return TransitionDecision(None, next_state)


def mark_alert_sent(
    state: MonitorState,
    alert_kind: AlertKind,
    *,
    sent_at: datetime,
) -> MonitorState:
    timestamp = sent_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return replace(state, last_alert_kind=alert_kind, last_alert_at=timestamp)


def _state_from_mapping(payload: Any) -> MonitorState:
    if not isinstance(payload, dict):
        raise StateError("state must be a JSON object")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise StateError(f"unsupported state schema: {payload.get('schema_version')!r}")

    try:
        last_confirmed_raw = payload.get("last_confirmed")
        last_confirmed = (
            Availability(last_confirmed_raw) if last_confirmed_raw is not None else None
        )
        if last_confirmed not in {None, Availability.AVAILABLE, Availability.UNAVAILABLE}:
            raise ValueError("last_confirmed must be available or unavailable")

        last_alert_kind_raw = payload.get("last_alert_kind")
        last_alert_kind = (
            AlertKind(last_alert_kind_raw) if last_alert_kind_raw is not None else None
        )
        unavailable_streak = payload.get("unavailable_streak", 0)
        queue_active = payload.get("queue_active", False)
        if not isinstance(unavailable_streak, int) or isinstance(unavailable_streak, bool):
            raise ValueError("unavailable_streak must be an integer")
        if unavailable_streak < 0 or unavailable_streak > 2:
            raise ValueError("unavailable_streak must be between 0 and 2")
        if not isinstance(queue_active, bool):
            raise ValueError("queue_active must be a boolean")

        heartbeat_month = _optional_string(payload, "heartbeat_month")
        if (
            heartbeat_month is not None
            and re.fullmatch(
                r"\d{4}-(?:0[1-9]|1[0-2])",
                heartbeat_month,
            )
            is None
        ):
            raise ValueError("heartbeat_month must use YYYY-MM format")

        return MonitorState(
            schema_version=STATE_SCHEMA_VERSION,
            last_confirmed=last_confirmed,
            unavailable_streak=unavailable_streak,
            queue_active=queue_active,
            queue_event_id=_optional_string(payload, "queue_event_id"),
            last_alert_kind=last_alert_kind,
            last_alert_at=_optional_string(payload, "last_alert_at"),
            heartbeat_month=heartbeat_month,
        )
    except (TypeError, ValueError) as exc:
        raise StateError(f"invalid monitor state: {exc}") from exc


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def load_state(path: Path) -> MonitorState:
    if not path.exists():
        return MonitorState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"could not read state file {path}: {exc}") from exc
    return _state_from_mapping(payload)


def _serialized_state(state: MonitorState) -> str:
    payload = asdict(state)
    payload["last_confirmed"] = (
        state.last_confirmed.value if state.last_confirmed is not None else None
    )
    payload["last_alert_kind"] = (
        state.last_alert_kind.value if state.last_alert_kind is not None else None
    )
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def save_state(path: Path, state: MonitorState) -> bool:
    serialized = _serialized_state(state)
    temporary_path: Path | None = None
    try:
        if path.exists() and path.read_text(encoding="utf-8") == serialized:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        raise StateError(f"could not save state file {path}: {exc}") from exc
    return True
