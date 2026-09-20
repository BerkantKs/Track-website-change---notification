import json
from pathlib import Path

import pytest

from nintendo_stock_monitor.models import AlertKind, Availability, MonitorState
from nintendo_stock_monitor.state import StateError, load_state, save_state


def test_missing_state_returns_default(tmp_path: Path) -> None:
    assert load_state(tmp_path / "missing.json") == MonitorState()


def test_state_round_trip_and_unchanged_save(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    state = MonitorState(
        last_confirmed=Availability.AVAILABLE,
        queue_active=True,
        queue_event_id="lrwrv2",
        last_alert_kind=AlertKind.AVAILABLE,
        last_alert_at="2026-09-20T12:00:00Z",
        heartbeat_month="2026-09",
    )

    assert save_state(path, state) is True
    assert load_state(path) == state
    assert save_state(path, state) is False


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(StateError, match="could not read state file"):
        load_state(path)


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    with pytest.raises(StateError, match="unsupported state schema"):
        load_state(path)


@pytest.mark.parametrize(
    "override",
    [
        {"last_confirmed": "queue"},
        {"last_alert_kind": "not-an-alert"},
        {"unavailable_streak": 3},
        {"queue_active": "yes"},
        {"heartbeat_month": "2026-13"},
    ],
)
def test_invalid_state_values_are_rejected(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema_version": 1, **override}),
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="invalid monitor state"):
        load_state(path)


def test_save_directory_failure_is_reported_as_state_error(tmp_path: Path) -> None:
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("content", encoding="utf-8")

    with pytest.raises(StateError, match="could not save state file"):
        save_state(parent_file / "state.json", MonitorState())
