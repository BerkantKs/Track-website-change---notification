import argparse
import json
import sys
from pathlib import Path
from typing import Any

from nintendo_stock_monitor.config import ConfigError, load_config
from nintendo_stock_monitor.monitor import run_check
from nintendo_stock_monitor.notifier import (
    NtfyError,
    NtfySettings,
    build_test_notification,
    publish_notification,
)
from nintendo_stock_monitor.state import StateError

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_STATE = 3
EXIT_NOTIFICATION = 4
EXIT_UNEXPECTED = 5


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor Nintendo Store availability")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Check the configured product once")
    check_parser.add_argument("--config", type=Path, default=Path("config.toml"))
    check_parser.add_argument("--state-file", type=Path, default=Path(".monitor/state.json"))
    check_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify only; do not notify or update state",
    )

    test_parser = subparsers.add_parser(
        "test-notification",
        help="Send a test message through ntfy",
    )
    test_parser.add_argument("--config", type=Path, default=Path("config.toml"))
    return parser


def _print_json(payload: dict[str, Any], *, error: bool = False) -> None:
    output = sys.stderr if error else sys.stdout
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True), file=output)


def _run_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ntfy_settings = None if args.dry_run else NtfySettings.from_environment()
    outcome = run_check(
        config,
        args.state_file,
        ntfy_settings=ntfy_settings,
        dry_run=args.dry_run,
    )
    _print_json(
        {
            "alert": outcome.alert_kind.value if outcome.alert_kind else None,
            "availability": outcome.result.availability.value,
            "dry_run": outcome.dry_run,
            "notification_sent": outcome.notification_sent,
            "queue_event_id": outcome.result.queue_event_id,
            "reason": outcome.result.reason.value,
            "state_changed": outcome.state_changed,
            "status_code": outcome.result.status_code,
        }
    )
    return EXIT_OK


def _run_test_notification(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    settings = NtfySettings.from_environment()
    publish_notification(settings, build_test_notification(config.product))
    _print_json({"notification_sent": True, "type": "test"})
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            return _run_check(args)
        if args.command == "test-notification":
            return _run_test_notification(args)
    except ConfigError as exc:
        _print_json({"error": "configuration", "message": str(exc)}, error=True)
        return EXIT_CONFIG
    except StateError as exc:
        _print_json({"error": "state", "message": str(exc)}, error=True)
        return EXIT_STATE
    except NtfyError as exc:
        _print_json({"error": "notification", "message": str(exc)}, error=True)
        return EXIT_NOTIFICATION
    except Exception as exc:
        _print_json(
            {"error": "unexpected", "message": f"{type(exc).__name__}: {exc}"},
            error=True,
        )
        return EXIT_UNEXPECTED
    return EXIT_UNEXPECTED


if __name__ == "__main__":
    raise SystemExit(main())
