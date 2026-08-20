from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from importlib.resources import files
import logging
from threading import Thread
import time
from typing import Any, Mapping

from . import __version__
from .config import LOG_MODE_DEFAULTS, logging_mode_defaults
from .logging_config import level_number
from .web_http import handler_class
from .web_state import (
    ApiError,
    DashboardLogHandler,
    MEMORY_LEVELS,
    TRAFFIC_LEVELS,
    _metric_values,
)

LOGGER = logging.getLogger("plcmock.web")


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
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
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
        self._attached = False
        self._assets: dict[str, bytes] = {}
        self._logging = {
            "mode": config.server.log_mode,
            "level": config.server.log_level,
            "traffic": config.server.traffic_log,
            "memory": config.server.memory_log,
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
        try:
            self._httpd = ThreadingHTTPServer((self.bind, self.port), handler_class(self))
        except OSError as exc:
            raise OSError(f"cannot bind web dashboard to {self.bind}:{self.port}: {exc}") from exc
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
            extra={"event": "web_started", "web_url": self.url, "web_write_access": self.allow_write},
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

    def status(self) -> dict[str, Any]:
        memory = self.plc_server.memory.describe()
        bound = dict(self.plc_server.bound_endpoints)
        metrics = self.log_handler.metrics()
        endpoints = []
        for item in self.config.endpoints:
            address = bound.get(item.name)
            endpoints.append(
                {
                    "name": item.name,
                    "protocol": item.protocol,
                    "configured_bind": item.bind,
                    "configured_port": item.port,
                    "bound_host": address[0] if address else None,
                    "bound_port": address[1] if address else None,
                    "running": address is not None,
                    "metrics": metrics["endpoints"].get(item.name, _metric_values({})),
                    "last_event": metrics["last_events"].get(item.name),
                }
            )
        address = self.bound_address or (self.bind, self.port)
        return {
            "ok": True,
            "version": __version__,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "uptime_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "config_source": str(self.config.source),
            "web": {
                "url": self.url,
                "bind": address[0],
                "port": address[1],
                "allow_write": self.allow_write,
                "max_memory_points": self.max_memory_points,
                "authentication": False,
            },
            "logging": dict(self._logging),
            "metrics": metrics["totals"],
            "endpoints": endpoints,
            "memory": {
                "words": [{"name": name, "size": size} for name, size in memory["words"].items()],
                "bits": [{"name": name, "size": size} for name, size in memory["bits"].items()],
            },
        }

    def read_memory(self, storage: str, area: str, start: int, count: int) -> dict[str, Any]:
        storage = _storage(storage)
        if not 1 <= count <= self.max_memory_points:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"count must be in 1..{self.max_memory_points}")
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
        return {"storage": storage, "area": area, "start": start, "count": count, "values": [int(v) for v in values]}

    def write_memory(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not self.allow_write:
            raise ApiError(HTTPStatus.FORBIDDEN, "memory writes are disabled for this web dashboard")
        storage = _storage(payload.get("storage"))
        area = _text(payload.get("area"), "area")
        if "items" in payload:
            raw_items = payload["items"]
            if not isinstance(raw_items, list) or not raw_items or len(raw_items) > self.max_memory_points:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"items must contain 1..{self.max_memory_points} entries")
            items: list[tuple[int, int]] = []
            seen: set[int] = set()
            for index, raw in enumerate(raw_items):
                if not isinstance(raw, Mapping):
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"items[{index}] must be an object")
                address = _integer(raw.get("address"), f"items[{index}].address")
                if address < 0 or address in seen:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "item addresses must be unique and non-negative")
                seen.add(address)
                items.append((address, _memory_value(storage, raw.get("value"))))
            try:
                target = self.plc_server.memory.word(area) if storage == "word" else self.plc_server.memory.bit(area)
                for address, _ in items:
                    target.read_words(address, 1) if storage == "word" else target.read_bits(address, 1)
                for address, value in items:
                    target.write_words(address, [value]) if storage == "word" else target.write_bits(address, [value])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            LOGGER.info(
                "web memory edit storage=%s area=%s cells=%d",
                storage,
                area,
                len(items),
                extra={"event": "web_memory_write", "memory_storage": storage, "memory_area": area, "cell_count": len(items)},
            )
            return {"ok": True, "storage": storage, "area": area, "items": [{"address": a, "value": v} for a, v in items]}

        start = _integer(payload.get("start"), "start")
        raw_values = payload.get("values")
        if start < 0 or not isinstance(raw_values, list) or not raw_values or len(raw_values) > self.max_memory_points:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"values must contain 1..{self.max_memory_points} entries and start must be non-negative")
        values = [_memory_value(storage, value) for value in raw_values]
        try:
            target = self.plc_server.memory.word(area) if storage == "word" else self.plc_server.memory.bit(area)
            target.read_words(start, len(values)) if storage == "word" else target.read_bits(start, len(values))
            target.write_words(start, values) if storage == "word" else target.write_bits(start, values)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        LOGGER.info(
            "web memory edit storage=%s area=%s start=%d count=%d",
            storage,
            area,
            start,
            len(values),
            extra={"event": "web_memory_write", "memory_storage": storage, "memory_area": area, "address": start, "count": len(values)},
        )
        return {"ok": True, "storage": storage, "area": area, "start": start, "count": len(values), "values": values}

    def set_log_mode(self, mode: Any) -> dict[str, str]:
        if not isinstance(mode, str) or mode.lower() not in LOG_MODE_DEFAULTS:
            raise ApiError(HTTPStatus.BAD_REQUEST, "mode must be one of: " + ", ".join(sorted(LOG_MODE_DEFAULTS)))
        mode = mode.lower()
        level, traffic, memory = logging_mode_defaults(mode)
        logging.getLogger("plcmock").setLevel(level_number(level))
        logging.getLogger("plcmock.traffic").setLevel(TRAFFIC_LEVELS[traffic])
        logging.getLogger("plcmock.memory").setLevel(MEMORY_LEVELS[memory])
        for endpoint in tuple(getattr(self.plc_server, "_endpoints", ())):
            endpoint.server_config = replace(
                endpoint.server_config,
                log_mode=mode,
                log_level=level,
                traffic_log=traffic,
                memory_log=memory,
                hex_dump=traffic == "hex",
            )
        self._logging = {"mode": mode, "level": level, "traffic": traffic, "memory": memory}
        LOGGER.warning(
            "runtime logging mode changed mode=%s level=%s traffic=%s memory=%s",
            mode,
            level,
            traffic,
            memory,
            extra={"event": "web_logging_changed", "log_mode": mode, "log_level": level, "traffic_log": traffic, "memory_log": memory},
        )
        return dict(self._logging)

    def asset(self, filename: str) -> bytes:
        if filename not in self._assets:
            try:
                self._assets[filename] = files("plcmock").joinpath("web", filename).read_bytes()
            except (FileNotFoundError, ModuleNotFoundError) as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, "asset not found") from exc
        return self._assets[filename]


def _storage(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, "storage must be word or bit")
    value = value.strip().lower()
    value = "word" if value in {"word", "words", "register", "registers"} else value
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
            raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid memory value {value!r}") from exc
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"invalid memory value {value!r}")
    maximum = 0xFFFF if storage == "word" else 1
    if not 0 <= number <= maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{storage} value must be in 0..{maximum}")
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
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be an integer") from exc
    raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be an integer")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{name} must be a non-empty string")
    return value.strip()
