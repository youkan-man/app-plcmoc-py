from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Mapping

from .logging_config import TRACE

STANDARD_LOG_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
    | {"message", "asctime", "request_id", "endpoint", "protocol", "remote"}
)
TRAFFIC_LEVELS = {"off": logging.CRITICAL + 1, "summary": logging.INFO, "hex": TRACE}
MEMORY_LEVELS = {"off": logging.CRITICAL + 1, "write": logging.DEBUG, "all": TRACE}
METRIC_DEFAULTS = {
    "received": 0,
    "sent": 0,
    "bytes_received": 0,
    "bytes_sent": 0,
    "no_response": 0,
    "errors": 0,
    "fault_drops": 0,
    "fault_corruptions": 0,
    "fault_duplicates": 0,
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class DashboardLogHandler(logging.Handler):
    """Bounded, thread-safe structured log buffer with traffic counters."""

    def __init__(self, capacity: int) -> None:
        super().__init__(TRACE)
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 100
        ):
            raise ValueError("web log buffer must contain at least 100 records")
        self.capacity = capacity
        self._items: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = Lock()
        self._next_id = 1
        self._totals: Counter[str] = Counter()
        self._per_endpoint: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self._last_event: dict[str, dict[str, Any]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "id": 0,
                "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "level_number": record.levelno,
                "logger": record.name,
                "message": record.getMessage(),
            }
            for name in ("event", "request_id", "endpoint", "protocol", "remote"):
                value = getattr(record, name, None)
                if value not in (None, ""):
                    entry[name] = _json_safe(value)
            fields = {
                name: _json_safe(value)
                for name, value in record.__dict__.items()
                if name not in STANDARD_LOG_FIELDS and not name.startswith("_")
            }
            if fields:
                entry["fields"] = fields
            if record.exc_info:
                entry["exception"] = logging.Formatter().formatException(record.exc_info)
            with self._lock:
                entry["id"] = self._next_id
                self._next_id += 1
                self._items.append(entry)
                self._count(entry)
        except Exception:
            self.handleError(record)

    def _count(self, entry: Mapping[str, Any]) -> None:
        event = str(entry.get("event", ""))
        endpoint = str(entry.get("endpoint", ""))
        fields = entry.get("fields", {})
        fields = fields if isinstance(fields, Mapping) else {}
        metric = {
            "datagram_received": "received",
            "datagram_sent": "sent",
            "no_response": "no_response",
            "protocol_exception": "errors",
            "datagram_task_failed": "errors",
            "datagram_rejected": "errors",
            "udp_error": "errors",
            "server_start_failed": "errors",
            "fault_drop": "fault_drops",
            "fault_corrupt": "fault_corruptions",
            "fault_duplicate": "fault_duplicates",
        }.get(event)
        if metric:
            self._totals[metric] += 1
            if endpoint:
                self._per_endpoint[endpoint][metric] += 1
        if event in {"datagram_received", "datagram_sent"}:
            name = "bytes_received" if event == "datagram_received" else "bytes_sent"
            amount = _non_negative_int(fields.get("payload_bytes"))
            self._totals[name] += amount
            if endpoint:
                self._per_endpoint[endpoint][name] += amount
        if endpoint:
            self._last_event[endpoint] = {
                "timestamp": entry.get("timestamp"),
                "event": event or None,
                "level": entry.get("level"),
                "message": entry.get("message"),
            }

    def query(
        self,
        *,
        after: int,
        limit: int,
        endpoint: str | None,
        minimum_level: int | None,
        search: str | None,
    ) -> dict[str, Any]:
        needle = search.casefold() if search else None
        with self._lock:
            snapshot = list(self._items)
            latest = self._next_id - 1
        result: list[dict[str, Any]] = []
        for item in snapshot:
            if item["id"] <= after:
                continue
            if endpoint and item.get("endpoint") != endpoint:
                continue
            if minimum_level is not None and item["level_number"] < minimum_level:
                continue
            if needle and needle not in json.dumps(item, ensure_ascii=False, default=str).casefold():
                continue
            result.append(item)
            if len(result) == limit:
                break
        next_after = result[-1]["id"] if result and len(result) == limit else latest
        return {
            "records": result,
            "latest_id": latest,
            "next_after": next_after,
            "oldest_id": snapshot[0]["id"] if snapshot else latest,
            "capacity": self.capacity,
        }

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            totals = _metric_values(self._totals)
            endpoints = {name: _metric_values(values) for name, values in self._per_endpoint.items()}
            last_events = {name: dict(value) for name, value in self._last_event.items()}
        return {"totals": totals, "endpoints": endpoints, "last_events": last_events}


def _metric_values(values: Mapping[str, Any]) -> dict[str, int]:
    return {name: _non_negative_int(values.get(name, 0)) for name in METRIC_DEFAULTS}


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value)) if not isinstance(value, bool) else 0
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Iterable):
        return [_json_safe(item) for item in value]
    return str(value)
