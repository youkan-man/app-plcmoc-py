from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from typing import Any, Iterable, Iterator

from .config import ServerConfig


TRACE = 5
logging.addLevelName(TRACE, "TRACE")

_REQUEST_ID: ContextVar[str | None] = ContextVar("plcmock_request_id", default=None)
_ENDPOINT: ContextVar[str | None] = ContextVar("plcmock_endpoint", default=None)
_PROTOCOL: ContextVar[str | None] = ContextVar("plcmock_protocol", default=None)
_REMOTE: ContextVar[str | None] = ContextVar("plcmock_remote", default=None)
_VALUE_PREVIEW_LIMIT = 16

_STANDARD_RECORD_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
    | {"message", "asctime", "request_id", "endpoint", "protocol", "remote"}
)


def _trace(
    self: logging.Logger,
    message: object,
    *args: object,
    **kwargs: Any,
) -> None:
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


if not hasattr(logging.Logger, "trace"):
    setattr(logging.Logger, "trace", _trace)


@contextmanager
def bind_log_context(
    *,
    request_id: str | None = None,
    endpoint: str | None = None,
    protocol: str | None = None,
    remote: str | None = None,
) -> Iterator[None]:
    tokens = (
        (_REQUEST_ID, _REQUEST_ID.set(request_id)),
        (_ENDPOINT, _ENDPOINT.set(endpoint)),
        (_PROTOCOL, _PROTOCOL.set(protocol)),
        (_REMOTE, _REMOTE.set(remote)),
    )
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _REQUEST_ID.get()
        if not hasattr(record, "endpoint"):
            record.endpoint = _ENDPOINT.get()
        if not hasattr(record, "protocol"):
            record.protocol = _PROTOCOL.get()
        if not hasattr(record, "remote"):
            record.remote = _REMOTE.get()
        return True


class TextLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = _timestamp(record.created)
        parts = [timestamp, f"{record.levelname:<8}", record.name]
        for label, attribute in (
            ("event", "event"),
            ("request", "request_id"),
            ("endpoint", "endpoint"),
            ("protocol", "protocol"),
            ("remote", "remote"),
        ):
            value = getattr(record, attribute, None)
            if value not in (None, "", "-"):
                parts.append(f"{label}={value}")
        parts.append(record.getMessage())
        text = " ".join(parts)
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            text += "\n" + self.formatStack(record.stack_info)
        return text


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("request_id", "endpoint", "protocol", "remote"):
            value = getattr(record, name, None)
            if value not in (None, ""):
                payload[name] = value
        for name, value in record.__dict__.items():
            if name in _STANDARD_RECORD_FIELDS or name.startswith("_"):
                continue
            payload[name] = _json_safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(config: ServerConfig) -> None:
    """Configure independent application, traffic, and memory log channels."""

    global _VALUE_PREVIEW_LIMIT
    _VALUE_PREVIEW_LIMIT = config.max_value_preview

    base_level = level_number(config.log_level)
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter: logging.Formatter
    if config.log_format == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = TextLogFormatter()
    context_filter = _ContextFilter()

    handlers: list[logging.Handler] = []
    if config.log_console:
        handlers.append(logging.StreamHandler(sys.stderr))
    if config.log_file is not None:
        _ensure_log_parent(config.log_file)
        handlers.append(
            RotatingFileHandler(
                config.log_file,
                maxBytes=config.log_rotate_max_bytes,
                backupCount=config.log_rotate_backup_count,
                encoding="utf-8",
            )
        )
    if not handlers:
        handlers.append(logging.NullHandler())

    for handler in handlers:
        handler.setLevel(TRACE)
        handler.addFilter(context_filter)
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Root controls third-party output. The plcmock children can intentionally
    # opt into more detail without turning asyncio and dependencies noisy.
    root.setLevel(base_level)
    logging.getLogger("plcmock").setLevel(base_level)
    logging.getLogger("plcmock.traffic").setLevel(
        {
            "off": logging.CRITICAL + 1,
            "summary": logging.INFO,
            "hex": TRACE,
        }[config.traffic_log]
    )
    logging.getLogger("plcmock.memory").setLevel(
        {
            "off": logging.CRITICAL + 1,
            "write": logging.DEBUG,
            "all": TRACE,
        }[config.memory_log]
    )
    logging.captureWarnings(True)


def level_number(name: str) -> int:
    normalized = name.upper()
    if normalized == "TRACE":
        return TRACE
    value = logging.getLevelName(normalized)
    if isinstance(value, int):
        return value
    raise ValueError(f"unknown logging level {name!r}")


def format_hex(data: bytes, limit: int) -> tuple[str, bool]:
    if limit <= 0:
        return "", bool(data)
    shown = data[:limit]
    return shown.hex(" "), len(shown) < len(data)


def preview_values(values: Iterable[Any]) -> tuple[list[Any], bool]:
    preview: list[Any] = []
    truncated = False
    for index, value in enumerate(values):
        if index >= _VALUE_PREVIEW_LIMIT:
            truncated = True
            break
        preview.append(value)
    return preview, truncated


def _ensure_log_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _timestamp(created: float) -> str:
    return datetime.fromtimestamp(created, timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)
