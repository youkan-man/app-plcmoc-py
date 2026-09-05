from __future__ import annotations

from collections import Counter, OrderedDict, deque
from datetime import datetime, timezone
from threading import Lock
import time
from typing import Any, Mapping


COUNTER_KEYS = (
    "received",
    "sent",
    "bytes_received",
    "bytes_sent",
    "no_response",
    "errors",
    "fault_drops",
    "fault_corruptions",
    "fault_duplicates",
    "rejected",
)
BUCKET_KEYS = (
    "received",
    "sent",
    "bytes_received",
    "bytes_sent",
    "no_response",
    "errors",
)


class EndpointTelemetry:
    """Thread-safe endpoint telemetry independent from configured log levels."""

    def __init__(self, name: str, *, history_seconds: int = 120, client_limit: int = 64) -> None:
        self.name = name
        self.history_seconds = max(10, history_seconds)
        self.client_limit = max(1, client_limit)
        self._lock = Lock()
        self._counters: Counter[str] = Counter()
        self._buckets: deque[dict[str, int]] = deque(maxlen=self.history_seconds)
        self._clients: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._running = False
        self._desired_running = True
        self._generation = 0
        self._started_at: str | None = None
        self._stopped_at: str | None = None
        self._bound_host: str | None = None
        self._bound_port: int | None = None
        self._active_requests = 0
        self._peak_active_requests = 0
        self._latency_total_ms = 0.0
        self._latency_samples = 0
        self._latency_max_ms = 0.0
        self._last_rx_at: str | None = None
        self._last_tx_at: str | None = None
        self._last_remote: str | None = None
        self._last_request_id: str | None = None
        self._last_request_summary: str | None = None
        self._last_response_summary: str | None = None
        self._last_duration_ms: float | None = None
        self._last_error: str | None = None

    def mark_desired(self, running: bool) -> None:
        with self._lock:
            self._desired_running = bool(running)

    def mark_started(self, bound: tuple[str, int]) -> None:
        now = _iso_now()
        with self._lock:
            self._running = True
            self._desired_running = True
            self._generation += 1
            self._started_at = now
            self._stopped_at = None
            self._bound_host = str(bound[0])
            self._bound_port = int(bound[1])
            self._last_error = None

    def mark_stopped(self, *, error: str | None = None, desired: bool | None = None) -> None:
        with self._lock:
            self._running = False
            if desired is not None:
                self._desired_running = bool(desired)
            self._stopped_at = _iso_now()
            self._bound_host = None
            self._bound_port = None
            self._active_requests = 0
            if error:
                self._last_error = error

    def received(self, size: int, remote: str, request_id: str) -> None:
        now = _iso_now()
        with self._lock:
            self._counters["received"] += 1
            self._counters["bytes_received"] += max(0, int(size))
            self._bump_bucket("received", 1)
            self._bump_bucket("bytes_received", max(0, int(size)))
            self._last_rx_at = now
            self._last_remote = remote
            self._last_request_id = request_id
            client = self._clients.pop(remote, None) or {
                "remote": remote,
                "requests": 0,
                "bytes_received": 0,
                "last_seen_at": now,
            }
            client["requests"] += 1
            client["bytes_received"] += max(0, int(size))
            client["last_seen_at"] = now
            self._clients[remote] = client
            while len(self._clients) > self.client_limit:
                self._clients.popitem(last=False)

    def describe_request(self, summary: str) -> None:
        with self._lock:
            self._last_request_summary = summary

    def request_started(self) -> None:
        with self._lock:
            self._active_requests += 1
            self._peak_active_requests = max(self._peak_active_requests, self._active_requests)

    def request_finished(self, duration_ms: float, response_summary: str | None = None) -> None:
        duration = max(0.0, float(duration_ms))
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._latency_total_ms += duration
            self._latency_samples += 1
            self._latency_max_ms = max(self._latency_max_ms, duration)
            self._last_duration_ms = duration
            if response_summary is not None:
                self._last_response_summary = response_summary

    def sent(self, size: int, *, response_summary: str | None = None) -> None:
        now = _iso_now()
        with self._lock:
            self._counters["sent"] += 1
            self._counters["bytes_sent"] += max(0, int(size))
            self._bump_bucket("sent", 1)
            self._bump_bucket("bytes_sent", max(0, int(size)))
            self._last_tx_at = now
            if response_summary is not None:
                self._last_response_summary = response_summary

    def no_response(self, *, fault_drop: bool = False) -> None:
        with self._lock:
            self._counters["no_response"] += 1
            self._bump_bucket("no_response", 1)
            if fault_drop:
                self._counters["fault_drops"] += 1

    def fault_corruption(self) -> None:
        with self._lock:
            self._counters["fault_corruptions"] += 1

    def fault_duplicate(self) -> None:
        with self._lock:
            self._counters["fault_duplicates"] += 1

    def rejected(self, message: str) -> None:
        with self._lock:
            self._counters["rejected"] += 1
            self._counters["errors"] += 1
            self._bump_bucket("errors", 1)
            self._last_error = message

    def error(self, message: str) -> None:
        with self._lock:
            self._counters["errors"] += 1
            self._bump_bucket("errors", 1)
            self._last_error = message

    def clear_error(self) -> None:
        with self._lock:
            self._last_error = None

    def reset_metrics(self) -> None:
        with self._lock:
            self._counters.clear()
            self._buckets.clear()
            self._clients.clear()
            self._active_requests = 0
            self._peak_active_requests = 0
            self._latency_total_ms = 0.0
            self._latency_samples = 0
            self._latency_max_ms = 0.0
            self._last_rx_at = None
            self._last_tx_at = None
            self._last_remote = None
            self._last_request_id = None
            self._last_request_summary = None
            self._last_response_summary = None
            self._last_duration_ms = None
            self._last_error = None

    def snapshot(self, *, history_seconds: int = 60) -> dict[str, Any]:
        now_second = int(time.time())
        with self._lock:
            counters = {key: int(self._counters.get(key, 0)) for key in COUNTER_KEYS}
            history_map = {bucket["second"]: dict(bucket) for bucket in self._buckets}
            history = []
            start = now_second - max(1, history_seconds) + 1
            for second in range(start, now_second + 1):
                source = history_map.get(second, {})
                history.append(
                    {
                        "second": second,
                        **{key: int(source.get(key, 0)) for key in BUCKET_KEYS},
                    }
                )
            recent = history[-5:]
            divisor = max(1, len(recent))
            rates = {
                "received_per_second": round(sum(item["received"] for item in recent) / divisor, 3),
                "sent_per_second": round(sum(item["sent"] for item in recent) / divisor, 3),
                "bytes_received_per_second": round(sum(item["bytes_received"] for item in recent) / divisor, 3),
                "bytes_sent_per_second": round(sum(item["bytes_sent"] for item in recent) / divisor, 3),
            }
            average_latency = (
                self._latency_total_ms / self._latency_samples
                if self._latency_samples
                else 0.0
            )
            clients = [dict(item) for item in reversed(self._clients.values())]
            return {
                "running": self._running,
                "desired_running": self._desired_running,
                "generation": self._generation,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "bound_host": self._bound_host,
                "bound_port": self._bound_port,
                "active_requests": self._active_requests,
                "peak_active_requests": self._peak_active_requests,
                "average_latency_ms": round(average_latency, 3),
                "max_latency_ms": round(self._latency_max_ms, 3),
                "latency_samples": self._latency_samples,
                "last_rx_at": self._last_rx_at,
                "last_tx_at": self._last_tx_at,
                "last_remote": self._last_remote,
                "last_request_id": self._last_request_id,
                "last_request_summary": self._last_request_summary,
                "last_response_summary": self._last_response_summary,
                "last_duration_ms": (
                    round(self._last_duration_ms, 3)
                    if self._last_duration_ms is not None
                    else None
                ),
                "last_error": self._last_error,
                "client_count": len(clients),
                "clients": clients,
                "metrics": counters,
                "rates": rates,
                "history": history,
            }

    def _bump_bucket(self, key: str, amount: int) -> None:
        second = int(time.time())
        if not self._buckets or self._buckets[-1]["second"] != second:
            self._buckets.append({"second": second})
        self._buckets[-1][key] = int(self._buckets[-1].get(key, 0)) + int(amount)


def aggregate_endpoint_snapshots(endpoints: list[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = {key: 0 for key in COUNTER_KEYS}
    rates = {
        "received_per_second": 0.0,
        "sent_per_second": 0.0,
        "bytes_received_per_second": 0.0,
        "bytes_sent_per_second": 0.0,
    }
    history_by_second: dict[int, dict[str, int]] = {}
    for endpoint in endpoints:
        source_metrics = endpoint.get("metrics", {})
        if isinstance(source_metrics, Mapping):
            for key in metrics:
                metrics[key] += int(source_metrics.get(key, 0))
        source_rates = endpoint.get("rates", {})
        if isinstance(source_rates, Mapping):
            for key in rates:
                rates[key] += float(source_rates.get(key, 0.0))
        source_history = endpoint.get("history", [])
        if isinstance(source_history, list):
            for item in source_history:
                if not isinstance(item, Mapping):
                    continue
                second = int(item.get("second", 0))
                target = history_by_second.setdefault(
                    second, {"second": second, **{key: 0 for key in BUCKET_KEYS}}
                )
                for key in BUCKET_KEYS:
                    target[key] += int(item.get(key, 0))
    return {
        "metrics": metrics,
        "rates": {key: round(value, 3) for key, value in rates.items()},
        "history": [history_by_second[key] for key in sorted(history_by_second)],
    }


def protocol_snapshot(plugin: Any) -> dict[str, Any]:
    """Return a small JSON-safe view of known protocol runtime state."""

    result: dict[str, Any] = {
        "name": str(getattr(plugin, "protocol_name", plugin.__class__.__name__)),
        "class": f"{plugin.__class__.__module__}.{plugin.__class__.__name__}",
    }
    name = result["name"]
    if name == "mc-protocol":
        qna = getattr(plugin, "qna", getattr(plugin, "slmp", None))
        one_e = getattr(plugin, "one_e", None)
        result["family"] = "mitsubishi-mc"
        result["qna"] = _attributes(
            qna,
            {
                "cpu_state",
                "last_clear_mode",
                "error_code",
                "model_name",
                "model_code",
                "accepted_frames",
                "accepted_encodings",
                "enabled_commands",
                "allow_remote_control",
            },
        )
        result["one_e"] = _attributes(
            one_e,
            {
                "accepted_frames",
                "accepted_encodings",
                "enabled_commands",
                "pc_number",
                "accept_any_pc",
                "max_points",
            },
        )
    elif name == "slmp":
        result["family"] = "mitsubishi-mc"
        result.update(
            _attributes(
                plugin,
                {
                    "cpu_state",
                    "last_clear_mode",
                    "error_code",
                    "model_name",
                    "model_code",
                    "accepted_frames",
                    "accepted_encodings",
                    "enabled_commands",
                    "allow_remote_control",
                },
            )
        )
    elif name == "mc-1e":
        result["family"] = "mitsubishi-mc"
        result.update(
            _attributes(
                plugin,
                {
                    "accepted_frames",
                    "accepted_encodings",
                    "enabled_commands",
                    "pc_number",
                    "accept_any_pc",
                    "max_points",
                },
            )
        )
    elif name == "fins-udp":
        result["family"] = "omron-fins"
        result.update(
            _attributes(
                plugin,
                {"node", "accept_any_destination", "max_elements"},
            )
        )
    elif name == "modbus-udp":
        result["family"] = "modbus"
        result.update(
            _attributes(
                plugin,
                {
                    "accepted_unit_ids",
                    "coils",
                    "discrete_inputs",
                    "holding_registers",
                    "input_registers",
                },
            )
        )
    else:
        result["family"] = "custom"
    return _json_safe(result)


def _attributes(target: Any, names: set[str]) -> dict[str, Any]:
    if target is None:
        return {}
    return {
        name: _json_safe(getattr(target, name))
        for name in sorted(names)
        if hasattr(target, name)
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_json_safe(item) for item in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    return str(value)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
