from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    QUEUE = "queue"
    UNKNOWN = "unknown"


class Reason(StrEnum):
    JSON_LD_IN_STOCK = "json_ld_in_stock"
    JSON_LD_OUT_OF_STOCK = "json_ld_out_of_stock"
    ENABLED_PURCHASE_CONTROL = "enabled_purchase_control"
    DISABLED_PURCHASE_CONTROL = "disabled_purchase_control"
    SOLD_OUT_TEXT = "sold_out_text"
    QUEUE_REDIRECT = "queue_redirect"
    NOT_FOUND = "not_found"
    AMBIGUOUS_PAGE = "ambiguous_page"
    UNEXPECTED_REDIRECT = "unexpected_redirect"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TRANSPORT_ERROR = "transport_error"
    UNEXPECTED_STATUS = "unexpected_status"


class AlertKind(StrEnum):
    AVAILABLE = "available"
    QUEUE = "queue"


@dataclass(frozen=True, slots=True)
class CheckResult:
    availability: Availability
    reason: Reason
    checked_at: datetime
    status_code: int | None = None
    final_url: str | None = None
    queue_event_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MonitorState:
    schema_version: int = 1
    last_confirmed: Availability | None = None
    unavailable_streak: int = 0
    queue_active: bool = False
    queue_event_id: str | None = None
    last_alert_kind: AlertKind | None = None
    last_alert_at: str | None = None
    heartbeat_month: str | None = None


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    alert_kind: AlertKind | None
    next_state: MonitorState
