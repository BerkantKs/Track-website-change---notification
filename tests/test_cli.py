from datetime import UTC, datetime
from pathlib import Path

from nintendo_stock_monitor import cli
from nintendo_stock_monitor.models import AlertKind, Availability, CheckResult, Reason
from nintendo_stock_monitor.monitor import RunOutcome
from nintendo_stock_monitor.notifier import NotificationError
from nintendo_stock_monitor.state import StateError


def test_dry_run_does_not_require_github_configuration(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fake_run_check(*args, **kwargs) -> RunOutcome:
        assert kwargs["github_settings"] is None
        assert kwargs["dry_run"] is True
        return RunOutcome(
            result=CheckResult(
                Availability.QUEUE,
                Reason.QUEUE_REDIRECT,
                datetime(2026, 9, 20, tzinfo=UTC),
                status_code=302,
                queue_event_id="lrwrv2",
            ),
            alert_kind=AlertKind.QUEUE,
            notification_sent=False,
            state_changed=False,
            dry_run=True,
        )

    monkeypatch.setattr(cli, "run_check", fake_run_check)

    exit_code = cli.main(
        [
            "check",
            "--dry-run",
            "--config",
            "config.toml",
            "--state-file",
            str(tmp_path / "state.json"),
        ]
    )

    assert exit_code == cli.EXIT_OK
    assert '"availability": "queue"' in capsys.readouterr().out


def test_missing_github_token_returns_config_exit_code(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    exit_code = cli.main(["check", "--config", "config.toml"])

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_CONFIG
    assert '"error": "configuration"' in captured.err


def test_state_failure_returns_state_exit_code(monkeypatch, capsys) -> None:
    def fail_run(*args, **kwargs):
        raise StateError("invalid state")

    monkeypatch.setattr(cli, "run_check", fail_run)

    exit_code = cli.main(["check", "--dry-run", "--config", "config.toml"])

    assert exit_code == cli.EXIT_STATE
    assert '"error": "state"' in capsys.readouterr().err


def test_notification_failure_returns_notification_exit_code(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repository")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "owner")

    def fail_publish(*args, **kwargs):
        raise NotificationError("GitHub Issue notification failed")

    monkeypatch.setattr(cli, "publish_notification", fail_publish)

    exit_code = cli.main(["test-notification", "--config", "config.toml"])

    assert exit_code == cli.EXIT_NOTIFICATION
    assert '"error": "notification"' in capsys.readouterr().err
