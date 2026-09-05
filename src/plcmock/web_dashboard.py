from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from importlib.resources import files
import logging
import os
import platform
try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]
import sys
from threading import Thread, active_count
import time
from typing import Any, Mapping

import yaml

from . import __version__
from .config import (
    LOG_FORMATS,
    LOG_LEVELS,
    LOG_MODE_DEFAULTS,
    MEMORY_LOG_MODES,
    TRAFFIC_LOG_MODES,
    EndpointConfig,
    logging_mode_defaults,
)
from .faults import FaultPolicy
from .logging_config import level_number
from .web_http import handler_class
from .web_state import (
    ApiError,
    DashboardLogHandler,
    MEMORY_LEVELS,
    TRAFFIC_LEVELS,
)

LOGGER = logging.getLogger("plcmock.web")


PROTOCOL_OPTION_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "mc": [
        {
            "path": "accepted_frames",
            "label": "Frames",
            "type": "multi",
            "choices": ["1E", "3E", "4E"],
            "default": ["1E", "3E", "4E"],
            "group": "Protocol profile",
        },
        {
            "path": "accepted_encodings",
            "label": "Encodings",
            "type": "multi",
            "choices": ["binary", "ascii"],
            "default": ["binary", "ascii"],
            "group": "Protocol profile",
        },
        {
            "path": "model_name",
            "label": "Model name",
            "type": "text",
            "default": "PLC MOCK",
            "group": "MC CPU",
        },
        {
            "path": "model_code",
            "label": "Model code",
            "type": "integer",
            "default": 0,
            "min": 0,
            "max": 65535,
            "group": "MC CPU",
        },
        {
            "path": "initial_state",
            "label": "Initial state",
            "type": "select",
            "choices": ["RUN", "STOP", "PAUSE"],
            "default": "RUN",
            "group": "MC CPU",
        },
        {
            "path": "allow_remote_control",
            "label": "Allow remote control",
            "type": "boolean",
            "default": True,
            "group": "MC CPU",
        },
        {
            "path": "reset_no_response",
            "label": "Reset has no response",
            "type": "boolean",
            "default": True,
            "group": "MC CPU",
        },
        {
            "path": "max_word_points",
            "label": "Max word points",
            "type": "integer",
            "default": 960,
            "min": 1,
            "max": 65535,
            "group": "Limits",
        },
        {
            "path": "max_bit_points_binary",
            "label": "Max binary bit points",
            "type": "integer",
            "default": 7168,
            "min": 1,
            "max": 65535,
            "group": "Limits",
        },
        {
            "path": "max_bit_points_ascii",
            "label": "Max ASCII bit points",
            "type": "integer",
            "default": 3584,
            "min": 1,
            "max": 65535,
            "group": "Limits",
        },
        {
            "path": "one_e_max_points",
            "label": "Max 1E points",
            "type": "integer",
            "default": 256,
            "min": 1,
            "max": 256,
            "group": "Limits",
        },
        {
            "path": "disabled_commands",
            "label": "Disabled 3E/4E commands",
            "type": "string-list",
            "default": [],
            "group": "Protocol profile",
        },
        {
            "path": "one_e_disabled_commands",
            "label": "Disabled 1E commands",
            "type": "string-list",
            "default": [],
            "group": "Protocol profile",
        },
    ],
    "fins": [
        {
            "path": "max_elements",
            "label": "Max elements",
            "type": "integer",
            "default": 999,
            "min": 1,
            "max": 65535,
            "group": "FINS",
        },
        {
            "path": "node",
            "label": "Node number",
            "type": "integer",
            "default": 1,
            "min": 0,
            "max": 255,
            "group": "FINS",
        },
        {
            "path": "accept_any_destination",
            "label": "Accept any destination",
            "type": "boolean",
            "default": True,
            "group": "FINS",
        },
    ],
    "modbus": [
        {
            "path": "accepted_unit_ids",
            "label": "Accepted unit IDs",
            "type": "integer-list",
            "default": [1, 2, 255],
            "nullable": True,
            "min": 0,
            "max": 255,
            "group": "Modbus",
        },
        {
            "path": "areas.coils",
            "label": "Coil area",
            "type": "text",
            "default": "M",
            "group": "Area mapping",
        },
        {
            "path": "areas.discrete_inputs",
            "label": "Discrete input area",
            "type": "text",
            "default": "X",
            "group": "Area mapping",
        },
        {
            "path": "areas.holding_registers",
            "label": "Holding register area",
            "type": "text",
            "default": "D",
            "group": "Area mapping",
        },
        {
            "path": "areas.input_registers",
            "label": "Input register area",
            "type": "text",
            "default": "INPUT",
            "group": "Area mapping",
        },
    ],
}


class WebDashboardServer:
    """Dependency-free dashboard and JSON API hosted beside the UDP server."""

    def __init__(
        self,
        config: Any,
        plc_server: Any,
        *,
        bind: str = "0.0.0.0",
        port: int = 8080,
        allow_write: bool = True,
        max_memory_points: int = 512,
        log_buffer_size: int = 2000,
    ) -> None:
        if not isinstance(bind, str) or not bind.strip():
            raise ValueError("web bind address must be a non-empty string")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 0 <= port <= 65535
        ):
            raise ValueError("web port must be in 0..65535")
        if (
            isinstance(max_memory_points, bool)
            or not isinstance(max_memory_points, int)
            or not 1 <= max_memory_points <= 65536
        ):
            raise ValueError("web max memory points must be in 1..65536")
        self.config = config
        self.plc_server = plc_server
        self.bind = bind.strip()
        self.port = port
        self.allow_write = bool(allow_write)
        self.max_memory_points = max_memory_points
        self.started_at = datetime.now(timezone.utc)
        self.started_monotonic = time.monotonic()
        self.log_handler = DashboardLogHandler(log_buffer_size)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._attached = False
        self._assets: dict[str, bytes] = {}
        self._logging = {
            "mode": config.server.log_mode,
            "level": config.server.log_level,
            "format": config.server.log_format,
            "traffic": config.server.traffic_log,
            "memory": config.server.memory_log,
            "max_hex_bytes": config.server.max_hex_bytes,
        }

    @property
    def bound_address(self) -> tuple[str, int] | None:
        if self._httpd is None:
            return None
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    @property
    def url(self) -> str | None:
        if self.bound_address is None:
            return None
        host, port = self.bound_address
        host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{host}:{port}"

    async def start(self) -> None:
        if self._httpd is not None:
            raise RuntimeError("web dashboard is already started")
        self._loop = asyncio.get_running_loop()
        try:
            self._httpd = ThreadingHTTPServer(
                (self.bind, self.port), handler_class(self)
            )
        except OSError as exc:
            raise OSError(
                f"cannot bind web dashboard to {self.bind}:{self.port}: {exc}"
            ) from exc
        self._httpd.daemon_threads = True
        logging.getLogger().addHandler(self.log_handler)
        self._attached = True
        self._thread = Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="plcmock-web",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "web dashboard listening url=%s write_access=%s",
            self.url,
            self.allow_write,
            extra={
                "event": "web_started",
                "web_url": self.url,
                "web_write_access": self.allow_write,
            },
        )

    async def close(self) -> None:
        httpd, self._httpd = self._httpd, None
        thread, self._thread = self._thread, None
        if httpd is not None:
            await asyncio.to_thread(httpd.shutdown)
            httpd.server_close()
            if thread is not None:
                thread.join(timeout=2)
            LOGGER.info("web dashboard stopped", extra={"event": "web_stopped"})
        if self._attached:
            logging.getLogger().removeHandler(self.log_handler)
            self._attached = False
        self._loop = None

    def health(self) -> dict[str, Any]:
        runtime = self.plc_server.runtime_snapshot()
        return {
            "ok": bool(runtime["healthy"]),
            "started": bool(runtime["started"]),
            "running_endpoints": runtime["running_endpoints"],
            "desired_endpoints": runtime["desired_endpoints"],
        }

    def status(self) -> dict[str, Any]:
        memory = self.plc_server.memory.describe()
        runtime = self.plc_server.runtime_snapshot()
        address = self.bound_address or (self.bind, self.port)
        warnings = self._warnings(runtime)
        total_words = sum(memory["words"].values())
        total_bits = sum(memory["bits"].values())
        return {
            "ok": True,
            "healthy": runtime["healthy"],
            "version": __version__,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_seconds": runtime["uptime_seconds"],
            "config_source": str(self.config.source),
            "web": {
                "url": self.url,
                "bind": address[0],
                "port": address[1],
                "allow_write": self.allow_write,
                "max_memory_points": self.max_memory_points,
                "authentication": False,
            },
            "system": _system_info(runtime),
            "logging": dict(self._logging),
            "metrics": runtime["metrics"],
            "rates": runtime["rates"],
            "history": runtime["history"],
            "running_endpoints": runtime["running_endpoints"],
            "desired_endpoints": runtime["desired_endpoints"],
            "endpoints": runtime["endpoints"],
            "warnings": warnings,
            "memory": {
                "words": [
                    {"name": name, "size": size}
                    for name, size in memory["words"].items()
                ],
                "bits": [
                    {"name": name, "size": size}
                    for name, size in memory["bits"].items()
                ],
                "total_word_points": total_words,
                "total_bit_points": total_bits,
                "estimated_bytes": total_words * 2 + total_bits,
            },
        }

    def settings(self) -> dict[str, Any]:
        runtime = self.plc_server.runtime_snapshot()
        endpoints: list[dict[str, Any]] = []
        for item in runtime["endpoints"]:
            protocol = item["protocol"]
            current = {
                "name": item["name"],
                "protocol": protocol,
                "bind": item["configured_bind"],
                "port": item["configured_port"],
                "options": deepcopy(item["options"]),
                "faults": deepcopy(item["faults"]),
                "running": item["desired_running"],
            }
            endpoints.append(
                {
                    "name": item["name"],
                    "running": item["running"],
                    "desired_running": item["desired_running"],
                    "changed_from_startup": _comparable_config(current)
                    != _comparable_config(item["startup"]),
                    "config": current,
                    "startup": deepcopy(item["startup"]),
                    "option_schema": _option_schema(protocol),
                    "metrics": deepcopy(item["metrics"]),
                    "rates": deepcopy(item["rates"]),
                    "last_error": item["last_error"],
                    "generation": item["generation"],
                }
            )
        return {
            "ok": True,
            "writable": self.allow_write,
            "runtime_only": True,
            "protocol_suggestions": [
                "mc-protocol",
                "slmp",
                "mc-1e",
                "fins-udp",
                "modbus-udp",
            ],
            "logging": {
                **self._logging,
                "modes": sorted(LOG_MODE_DEFAULTS),
                "levels": sorted(LOG_LEVELS, key=level_number),
                "formats": sorted(LOG_FORMATS),
                "traffic_modes": sorted(TRAFFIC_LOG_MODES),
                "memory_modes": sorted(MEMORY_LOG_MODES),
            },
            "endpoints": endpoints,
        }

    def apply_endpoint(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_write("endpoint configuration")
        current = next(
            (
                item
                for item in self.plc_server.current_endpoint_configs()
                if item.name == name
            ),
            None,
        )
        if current is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"unknown endpoint {name!r}")
        config = _endpoint_config(name, payload, current)
        running = _boolean(payload.get("running", True), "running")
        try:
            endpoint = self._run_server(
                self.plc_server.apply_endpoint(name, config, running=running)
            )
        except KeyError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
        return {"ok": True, "endpoint": endpoint, "settings": self.settings()}

    def endpoint_action(self, name: str, action: Any) -> dict[str, Any]:
        self._require_write("endpoint actions")
        action = _text(action, "action")
        try:
            endpoint = self._run_server(
                self.plc_server.endpoint_action(name, action)
            )
        except KeyError as exc:
            raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise ApiError(HTTPStatus.CONFLICT, str(exc)) from exc
        return {"ok": True, "endpoint": endpoint, "settings": self.settings()}

    def reset_metrics(self) -> dict[str, Any]:
        self._require_write("telemetry reset")
        self.plc_server.reset_all_metrics()
        return {"ok": True, "metrics": self.plc_server.runtime_snapshot()["metrics"]}

    def export_config(self) -> bytes:
        server_config = self.plc_server.server_config
        payload = {
            "server": {
                "max_datagram_size": server_config.max_datagram_size,
                "logging": {
                    "mode": self._logging["mode"],
                    "level": self._logging["level"],
                    "format": self._logging["format"],
                    "console": server_config.log_console,
                    "file": (
                        str(server_config.log_file)
                        if server_config.log_file is not None
                        else None
                    ),
                    "rotate_max_bytes": server_config.log_rotate_max_bytes,
                    "rotate_backup_count": server_config.log_rotate_backup_count,
                    "traffic": self._logging["traffic"],
                    "memory": self._logging["memory"],
                    "max_hex_bytes": self._logging["max_hex_bytes"],
                    "max_value_preview": server_config.max_value_preview,
                },
            },
            "plugin_paths": [str(path) for path in self.config.plugin_paths],
            "memory": deepcopy(self.config.memory),
            "endpoints": [
                {
                    "name": item.name,
                    "protocol": item.protocol,
                    "bind": item.bind,
                    "port": item.port,
                    "options": deepcopy(item.options),
                    "faults": FaultPolicy.from_mapping(item.faults).to_mapping(),
                }
                for item in self.plc_server.current_endpoint_configs()
            ],
        }
        return yaml.safe_dump(
            payload,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).encode("utf-8")

    def read_memory(
        self, storage: str, area: str, start: int, count: int
    ) -> dict[str, Any]:
        storage = _storage(storage)
        if not 1 <= count <= self.max_memory_points:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                f"count must be in 1..{self.max_memory_points}",
            )
        if start < 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "start must be non-negative")
        try:
            values = (
                self.plc_server.memory.word(area).read_words(start, count)
                if storage == "word"
                else self.plc_server.memory.bit(area).read_bits(start, count)
            )
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        return {
            "storage": storage,
            "area": area,
            "start": start,
            "count": count,
            "values": [int(value) for value in values],
        }

    def write_memory(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_write("memory writes")
        storage = _storage(payload.get("storage"))
        area = _text(payload.get("area"), "area")
        if "items" in payload:
            raw_items = payload["items"]
            if (
                not isinstance(raw_items, list)
                or not raw_items
                or len(raw_items) > self.max_memory_points
            ):
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    f"items must contain 1..{self.max_memory_points} entries",
                )
            items: list[tuple[int, int]] = []
            seen: set[int] = set()
            for index, raw in enumerate(raw_items):
                if not isinstance(raw, Mapping):
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        f"items[{index}] must be an object",
                    )
                address = _integer(raw.get("address"), f"items[{index}].address")
                if address < 0 or address in seen:
                    raise ApiError(
                        HTTPStatus.BAD_REQUEST,
                        "item addresses must be unique and non-negative",
                    )
                seen.add(address)
                items.append(
                    (address, _memory_value(storage, raw.get("value")))
                )
            try:
                target = (
                    self.plc_server.memory.word(area)
                    if storage == "word"
                    else self.plc_server.memory.bit(area)
                )
                for address, _ in items:
                    (
                        target.read_words(address, 1)
                        if storage == "word"
                        else target.read_bits(address, 1)
                    )
                for address, value in items:
                    (
                        target.write_words(address, [value])
                        if storage == "word"
                        else target.write_bits(address, [value])
                    )
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            LOGGER.info(
                "web memory edit storage=%s area=%s cells=%d",
                storage,
                area,
                len(items),
                extra={
                    "event": "web_memory_write",
                    "memory_storage": storage,
                    "memory_area": area,
                    "cell_count": len(items),
                },
            )
            return {
                "ok": True,
                "storage": storage,
                "area": area,
                "items": [
                    {"address": address, "value": value}
                    for address, value in items
                ],
            }

        start = _integer(payload.get("start"), "start")
        raw_values = payload.get("values")
        if (
            start < 0
            or not isinstance(raw_values, list)
            or not raw_values
            or len(raw_values) > self.max_memory_points
        ):
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                f"values must contain 1..{self.max_memory_points} entries "
                "and start must be non-negative",
            )
        values = [_memory_value(storage, value) for value in raw_values]
        try:
            target = (
                self.plc_server.memory.word(area)
                if storage == "word"
                else self.plc_server.memory.bit(area)
            )
            (
                target.read_words(start, len(values))
                if storage == "word"
                else target.read_bits(start, len(values))
            )
            (
                target.write_words(start, values)
                if storage == "word"
                else target.write_bits(start, values)
            )
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        LOGGER.info(
            "web memory edit storage=%s area=%s start=%d count=%d",
            storage,
            area,
            start,
            len(values),
            extra={
                "event": "web_memory_write",
                "memory_storage": storage,
                "memory_area": area,
                "address": start,
                "count": len(values),
            },
        )
        return {
            "ok": True,
            "storage": storage,
            "area": area,
            "start": start,
            "count": len(values),
            "values": values,
        }

    def set_log_mode(self, mode: Any) -> dict[str, Any]:
        return self.set_logging({"mode": mode})

    def set_logging(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_write("logging changes")
        if not isinstance(payload, Mapping):
            raise ApiError(HTTPStatus.BAD_REQUEST, "logging payload must be an object")
        current = self.plc_server.server_config
        mode = str(payload.get("mode", self._logging["mode"])).lower()
        if mode not in LOG_MODE_DEFAULTS:
            raise ApiError(
                HTTPStatus.BAD_REQUEST,
                "mode must be one of: " + ", ".join(sorted(LOG_MODE_DEFAULTS)),
            )
        level, traffic, memory = logging_mode_defaults(mode)
        level = str(payload.get("level", level)).upper()
        traffic = str(payload.get("traffic", traffic)).lower()
        memory = str(payload.get("memory", memory)).lower()
        log_format = current.log_format
        try:
            level_value = level_number(level)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if traffic not in TRAFFIC_LOG_MODES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid traffic logging mode")
        if memory not in MEMORY_LOG_MODES:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid memory logging mode")
        if log_format not in LOG_FORMATS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid log format")
        max_hex_bytes = _integer(
            payload.get("max_hex_bytes", current.max_hex_bytes),
            "max_hex_bytes",
        )
        if not 0 <= max_hex_bytes <= 65535:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, "max_hex_bytes must be in 0..65535"
            )

        logging.getLogger("plcmock").setLevel(level_value)
        logging.getLogger("plcmock.traffic").setLevel(TRAFFIC_LEVELS[traffic])
        logging.getLogger("plcmock.memory").setLevel(MEMORY_LEVELS[memory])
        updated = replace(
            current,
            log_mode=mode,
            log_level=level,
            log_format=log_format,
            traffic_log=traffic,
            memory_log=memory,
            max_hex_bytes=max_hex_bytes,
            hex_dump=traffic == "hex",
        )
        self.plc_server.update_server_config(updated)
        self._logging = {
            "mode": mode,
            "level": level,
            "format": log_format,
            "traffic": traffic,
            "memory": memory,
            "max_hex_bytes": max_hex_bytes,
        }
        LOGGER.warning(
            "runtime logging changed mode=%s level=%s traffic=%s memory=%s",
            mode,
            level,
            traffic,
            memory,
            extra={
                "event": "web_logging_changed",
                "log_mode": mode,
                "log_level": level,
                "traffic_log": traffic,
                "memory_log": memory,
            },
        )
        return {
            "mode": mode,
            "level": level,
            "traffic": traffic,
            "memory": memory,
        }

    def asset(self, filename: str) -> bytes:
        if filename not in self._assets:
            try:
                self._assets[filename] = (
                    files("plcmock").joinpath("web", filename).read_bytes()
                )
            except (FileNotFoundError, ModuleNotFoundError) as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, "asset not found") from exc
        return self._assets[filename]

    def _run_server(self, coroutine: Any) -> Any:
        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            close = getattr(coroutine, "close", None)
            if callable(close):
                close()
            raise ApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "PLC event loop is not available",
            )
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=15)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ApiError(
                HTTPStatus.GATEWAY_TIMEOUT,
                "endpoint operation timed out",
            ) from exc

    def _require_write(self, operation: str) -> None:
        if not self.allow_write:
            raise ApiError(
                HTTPStatus.FORBIDDEN,
                f"{operation} are disabled for this web dashboard",
            )

    def _warnings(self, runtime: Mapping[str, Any]) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        if self.allow_write:
            warnings.append(
                {
                    "severity": "warning",
                    "code": "unauthenticated-write-access",
                    "message": "Web authentication is disabled and runtime writes are enabled.",
                }
            )
        else:
            warnings.append(
                {
                    "severity": "info",
                    "code": "read-only-web",
                    "message": "The web dashboard is running in read-only mode.",
                }
            )
        for endpoint in runtime.get("endpoints", []):
            if endpoint.get("desired_running") and not endpoint.get("running"):
                warnings.append(
                    {
                        "severity": "error",
                        "code": "endpoint-down",
                        "message": f"Endpoint {endpoint['name']} is expected to run but is down.",
                    }
                )
            faults = endpoint.get("faults", {})
            if _faults_enabled(faults):
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "fault-injection-enabled",
                        "message": f"Fault injection is enabled for {endpoint['name']}.",
                    }
                )
        return warnings


def _option_schema(protocol: str) -> list[dict[str, Any]]:
    key = protocol.lower()
    if key in {"mc", "mc-protocol"}:
        return deepcopy(PROTOCOL_OPTION_SCHEMAS["mc"])
    if key in {"slmp", "slmp-3e-4e"}:
        result = [
            deepcopy(item)
            for item in PROTOCOL_OPTION_SCHEMAS["mc"]
            if not item["path"].startswith("one_e_")
        ]
        for item in result:
            if item["path"] == "accepted_frames":
                item["default"] = ["3E", "4E"]
        return result
    if key == "mc-1e":
        allowed = {
            "accepted_frames",
            "accepted_encodings",
            "one_e_max_points",
            "one_e_disabled_commands",
        }
        result = [
            deepcopy(item)
            for item in PROTOCOL_OPTION_SCHEMAS["mc"]
            if item["path"] in allowed
        ]
        for item in result:
            if item["path"] == "accepted_frames":
                item["default"] = ["1E"]
        return result
    if key in {"fins", "fins-udp"}:
        return deepcopy(PROTOCOL_OPTION_SCHEMAS["fins"])
    if key == "modbus-udp":
        return deepcopy(PROTOCOL_OPTION_SCHEMAS["modbus"])
    return []


def _endpoint_config(
    name: str,
    payload: Mapping[str, Any],
    current: EndpointConfig,
) -> EndpointConfig:
    protocol = _text(payload.get("protocol", current.protocol), "protocol")
    bind = _text(payload.get("bind", current.bind), "bind")
    port = _integer(payload.get("port", current.port), "port")
    if not 0 <= port <= 65535:
        raise ApiError(HTTPStatus.BAD_REQUEST, "port must be in 0..65535")
    options = payload.get("options", current.options)
    faults = payload.get("faults", current.faults)
    if not isinstance(options, Mapping):
        raise ApiError(HTTPStatus.BAD_REQUEST, "options must be an object")
    if not isinstance(faults, Mapping):
        raise ApiError(HTTPStatus.BAD_REQUEST, "faults must be an object")
    options_copy = deepcopy(dict(options))
    try:
        faults_copy = FaultPolicy.from_mapping(faults).to_mapping()
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    try:
        yaml.safe_dump(options_copy)
    except (TypeError, ValueError, yaml.YAMLError) as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"options are not serializable: {exc}") from exc
    return EndpointConfig(
        name=name,
        protocol=protocol,
        bind=bind,
        port=port,
        options=options_copy,
        faults=faults_copy,
    )


def _system_info(runtime: Mapping[str, Any]) -> dict[str, Any]:
    try:
        load_average = list(os.getloadavg())
    except (AttributeError, OSError):
        load_average = None
    max_rss = 0
    if resource is not None:
        try:
            raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            max_rss = raw if sys.platform == "darwin" else raw * 1024
        except (AttributeError, OSError, ValueError):
            pass
    return {
        "pid": os.getpid(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "hostname": platform.node(),
        "threads": active_count(),
        "active_requests": sum(
            int(item.get("active_requests", 0))
            for item in runtime.get("endpoints", [])
        ),
        "max_rss_bytes": max_rss,
        "load_average": load_average,
    }


def _faults_enabled(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    delay = value.get("delay_ms", {})
    delay_max = delay.get("max", 0) if isinstance(delay, Mapping) else delay
    return any(
        float(value.get(name, 0) or 0) > 0
        for name in ("drop_rate", "duplicate_rate", "corrupt_rate")
    ) or float(delay_max or 0) > 0


def _comparable_config(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "protocol": value.get("protocol"),
        "bind": value.get("bind"),
        "port": value.get("port"),
        "options": value.get("options", {}),
        "faults": FaultPolicy.from_mapping(value.get("faults", {})).to_mapping(),
        "running": bool(value.get("running", True)),
    }


def _storage(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "storage must be word or bit")
    value = value.strip().lower()
    value = (
        "word"
        if value in {"word", "words", "register", "registers"}
        else value
    )
    value = "bit" if value in {"bit", "bits", "coil", "coils"} else value
    if value not in {"word", "bit"}:
        raise ApiError(HTTPStatus.BAD_REQUEST, "storage must be word or bit")
    return value


def _memory_value(storage: str, value: Any) -> int:
    if isinstance(value, bool):
        number = int(value)
    elif isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value.strip(), 0)
        except ValueError as exc:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, f"invalid memory value {value!r}"
            ) from exc
    else:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"invalid memory value {value!r}"
        )
    maximum = 0xFFFF if storage == "word" else 1
    if not 0 <= number <= maximum:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"{storage} value must be in 0..{maximum}",
        )
    return number


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError as exc:
            raise ApiError(
                HTTPStatus.BAD_REQUEST, f"{name} must be an integer"
            ) from exc
    raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be an integer")


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be true or false")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"{name} must be a non-empty string"
        )
    return value.strip()
