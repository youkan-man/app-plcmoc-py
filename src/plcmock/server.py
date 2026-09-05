from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
from itertools import count
import logging
from threading import RLock
import time
from typing import Any

from .config import AppConfig, EndpointConfig, ServerConfig
from .diagnostics import DatagramDescription, describe_request, describe_response
from .faults import FaultPolicy
from .logging_config import TRACE, bind_log_context, format_hex
from .memory import MemorySpace
from .protocols.base import DatagramContext, ProtocolResponse
from .protocols.loader import load_protocol
from .runtime import (
    EndpointTelemetry,
    aggregate_endpoint_snapshots,
    protocol_snapshot,
)


LOGGER = logging.getLogger("plcmock.server")
TRAFFIC_LOGGER = logging.getLogger("plcmock.traffic")


class _EndpointProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        endpoint: EndpointConfig,
        plugin: Any,
        faults: FaultPolicy,
        telemetry: EndpointTelemetry,
        *,
        server_config: ServerConfig,
    ) -> None:
        self.endpoint = endpoint
        self.plugin = plugin
        self.faults = faults
        self.telemetry = telemetry
        self.server_config = server_config
        self.max_datagram_size = server_config.max_datagram_size
        self.transport: asyncio.DatagramTransport | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.closed = asyncio.Event()
        self._request_sequence = count(1)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        raw = transport.get_extra_info("sockname")
        bound = (str(raw[0]), int(raw[1]))
        self.telemetry.mark_started(bound)
        LOGGER.info(
            "UDP endpoint listening address=%s",
            raw,
            extra={
                "event": "endpoint_started",
                "endpoint": self.endpoint.name,
                "protocol": self.plugin.protocol_name,
                "bind": self.endpoint.bind,
                "port": self.endpoint.port,
                "bound_host": bound[0],
                "bound_port": bound[1],
            },
        )

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        request_id = f"{self.endpoint.name}-{next(self._request_sequence):08d}"
        remote = _format_address(addr)
        self.telemetry.received(len(data), remote, request_id)
        if len(data) > self.max_datagram_size:
            message = (
                f"oversized datagram: {len(data)} bytes exceeds "
                f"{self.max_datagram_size}"
            )
            self.telemetry.rejected(message)
            with bind_log_context(
                request_id=request_id,
                endpoint=self.endpoint.name,
                protocol=self.plugin.protocol_name,
                remote=remote,
            ):
                LOGGER.warning(
                    "rejected oversized datagram bytes=%d limit=%d",
                    len(data),
                    self.max_datagram_size,
                    extra={
                        "event": "datagram_rejected",
                        "payload_bytes": len(data),
                        "limit_bytes": self.max_datagram_size,
                    },
                )
            return
        task = asyncio.create_task(
            self._handle(data, addr, request_id),
            name=f"plcmock:{self.endpoint.name}:{request_id}",
        )
        self.tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            return
        try:
            exception = task.exception()
        except asyncio.CancelledError:
            return
        if exception is not None:
            self.telemetry.error(
                f"datagram task failed: {type(exception).__name__}: {exception}"
            )
            LOGGER.error(
                "datagram task failed: %s",
                exception,
                exc_info=(type(exception), exception, exception.__traceback__),
                extra={
                    "event": "datagram_task_failed",
                    "endpoint": self.endpoint.name,
                    "protocol": self.plugin.protocol_name,
                },
            )

    def error_received(self, exc: Exception) -> None:
        self.telemetry.error(f"UDP transport error: {type(exc).__name__}: {exc}")
        LOGGER.warning(
            "UDP transport error: %s",
            exc,
            extra={
                "event": "udp_error",
                "endpoint": self.endpoint.name,
                "protocol": self.plugin.protocol_name,
            },
        )

    def connection_lost(self, exc: Exception | None) -> None:
        message = f"{type(exc).__name__}: {exc}" if exc else None
        self.telemetry.mark_stopped(error=message)
        if exc:
            LOGGER.warning(
                "UDP endpoint closed with error: %s",
                exc,
                extra={
                    "event": "endpoint_closed",
                    "endpoint": self.endpoint.name,
                    "protocol": self.plugin.protocol_name,
                },
            )
        else:
            LOGGER.info(
                "UDP endpoint closed",
                extra={
                    "event": "endpoint_closed",
                    "endpoint": self.endpoint.name,
                    "protocol": self.plugin.protocol_name,
                },
            )
        self.closed.set()

    async def _handle(
        self,
        data: bytes,
        remote_address: tuple[str, int],
        request_id: str,
    ) -> None:
        transport = self.transport
        if transport is None:
            return
        local = transport.get_extra_info("sockname")
        local_address = (str(local[0]), int(local[1]))
        remote = _format_address(remote_address)
        context = DatagramContext(
            self.endpoint.name,
            local_address,
            remote_address,
            time.monotonic(),
        )
        started = time.perf_counter()
        response_summary: str | None = None
        self.telemetry.request_started()
        try:
            request_description = describe_request(self.plugin, data)
            self.telemetry.describe_request(request_description.summary)

            with bind_log_context(
                request_id=request_id,
                endpoint=self.endpoint.name,
                protocol=self.plugin.protocol_name,
                remote=remote,
            ):
                self._log_datagram(
                    direction="rx",
                    data=data,
                    description=request_description,
                    destination=None,
                    duration_ms=None,
                )

                if self.faults.should_drop():
                    self.telemetry.no_response(fault_drop=True)
                    TRAFFIC_LOGGER.warning(
                        "request dropped by configured fault policy",
                        extra={
                            "event": "fault_drop",
                            "direction": "rx",
                            "payload_bytes": len(data),
                            **request_description.fields,
                        },
                    )
                    return

                try:
                    result = self.plugin.handle_datagram(data, context)
                    if inspect.isawaitable(result):
                        result = await result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.telemetry.error(
                        f"protocol exception: {type(exc).__name__}: {exc}"
                    )
                    LOGGER.exception(
                        "protocol handler raised an unhandled exception: %s",
                        request_description.summary,
                        extra={
                            "event": "protocol_exception",
                            **request_description.fields,
                        },
                    )
                    return

                duration_ms = (time.perf_counter() - started) * 1000.0
                if result is None:
                    self.telemetry.no_response()
                    TRAFFIC_LOGGER.info(
                        "no response: %s duration_ms=%.3f",
                        request_description.summary,
                        duration_ms,
                        extra={
                            "event": "no_response",
                            "duration_ms": round(duration_ms, 3),
                            **request_description.fields,
                        },
                    )
                    return
                if isinstance(result, bytes):
                    response = ProtocolResponse(result)
                elif isinstance(result, ProtocolResponse):
                    response = result
                else:
                    message = (
                        "protocol handler returned unsupported response "
                        f"type={type(result).__name__}"
                    )
                    self.telemetry.error(message)
                    LOGGER.error(
                        message,
                        extra={
                            "event": "invalid_protocol_response",
                            "response_type": type(result).__name__,
                            **request_description.fields,
                        },
                    )
                    return

                fault_delay = self.faults.delay_seconds()
                response_delay = max(0.0, response.delay_ms) / 1000.0
                total_delay = fault_delay + response_delay
                if total_delay:
                    LOGGER.debug(
                        "delaying response delay_ms=%.3f fault_delay_ms=%.3f protocol_delay_ms=%.3f",
                        total_delay * 1000.0,
                        fault_delay * 1000.0,
                        response_delay * 1000.0,
                        extra={
                            "event": "response_delay",
                            "delay_ms": round(total_delay * 1000.0, 3),
                            "fault_delay_ms": round(fault_delay * 1000.0, 3),
                            "protocol_delay_ms": round(response_delay * 1000.0, 3),
                        },
                    )
                    await asyncio.sleep(total_delay)

                destination = response.destination or remote_address
                payload = self.faults.maybe_corrupt(response.payload)
                corrupted = payload != response.payload
                if corrupted:
                    self.telemetry.fault_corruption()
                    TRAFFIC_LOGGER.warning(
                        "response corrupted by configured fault policy",
                        extra={
                            "event": "fault_corrupt",
                            "direction": "tx",
                            "payload_bytes": len(payload),
                        },
                    )

                duplicate = self.faults.should_duplicate()
                response_description = describe_response(self.plugin, data, payload)
                response_summary = response_description.summary
                duration_ms = (time.perf_counter() - started) * 1000.0
                self._log_datagram(
                    direction="tx",
                    data=payload,
                    description=response_description,
                    destination=destination,
                    duration_ms=duration_ms,
                )
                transport.sendto(payload, destination)
                self.telemetry.sent(
                    len(payload), response_summary=response_description.summary
                )
                if duplicate:
                    transport.sendto(payload, destination)
                    self.telemetry.fault_duplicate()
                    self.telemetry.sent(
                        len(payload), response_summary=response_description.summary
                    )
                    TRAFFIC_LOGGER.warning(
                        "response duplicated by configured fault policy",
                        extra={
                            "event": "fault_duplicate",
                            "direction": "tx",
                            "destination": _format_address(destination),
                            "payload_bytes": len(payload),
                        },
                    )
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self.telemetry.request_finished(duration_ms, response_summary)

    def _log_datagram(
        self,
        *,
        direction: str,
        data: bytes,
        description: DatagramDescription,
        destination: tuple[str, int] | None,
        duration_ms: float | None,
    ) -> None:
        fields: dict[str, Any] = {
            **description.fields,
            "event": "datagram_received" if direction == "rx" else "datagram_sent",
            "direction": direction,
            "payload_bytes": len(data),
        }
        if destination is not None:
            fields["destination"] = _format_address(destination)
        if duration_ms is not None:
            fields["duration_ms"] = round(duration_ms, 3)
        suffix = f" duration_ms={duration_ms:.3f}" if duration_ms is not None else ""
        TRAFFIC_LOGGER.info(
            "%s bytes=%d%s",
            description.summary,
            len(data),
            suffix,
            extra=fields,
        )
        if self.server_config.traffic_log == "hex":
            hex_text, truncated = format_hex(
                data, self.server_config.max_hex_bytes
            )
            TRAFFIC_LOGGER.log(
                TRACE,
                "%s hex=%s%s",
                direction,
                hex_text,
                " ..." if truncated else "",
                extra={
                    "event": "datagram_hex",
                    "direction": direction,
                    "payload_bytes": len(data),
                    "hex": hex_text,
                    "hex_truncated": truncated,
                },
            )

    async def shutdown(self) -> None:
        if self.transport is not None:
            self.transport.close()
        if self.tasks:
            for task in tuple(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(self.closed.wait(), timeout=1.0)
        except TimeoutError:
            self.telemetry.mark_stopped(error="UDP transport close timed out")


class UdpMockServer:
    """UDP endpoint host with live endpoint management and telemetry."""

    def __init__(self, config: AppConfig, memory: MemorySpace | None = None) -> None:
        self.config = config
        self.memory = memory or MemorySpace.from_config(config.memory)
        self._server_config = config.server
        self._startup_configs = {item.name: item for item in config.endpoints}
        self._configs = {item.name: item for item in config.endpoints}
        self._telemetry = {
            item.name: EndpointTelemetry(item.name) for item in config.endpoints
        }
        self._endpoints: list[_EndpointProtocol] = []
        self._transports: list[asyncio.DatagramTransport] = []
        self._state_lock = RLock()
        self._management_lock = asyncio.Lock()
        self._started = False
        self._closing = False
        self.started_monotonic = time.monotonic()

    @property
    def bound_endpoints(self) -> dict[str, tuple[str, int]]:
        result: dict[str, tuple[str, int]] = {}
        with self._state_lock:
            endpoints = tuple(self._endpoints)
        for endpoint in endpoints:
            if endpoint.transport is not None:
                raw = endpoint.transport.get_extra_info("sockname")
                if raw:
                    result[endpoint.endpoint.name] = (str(raw[0]), int(raw[1]))
        return result

    @property
    def server_config(self) -> ServerConfig:
        return self._server_config

    def update_server_config(self, config: ServerConfig) -> None:
        self._server_config = config
        with self._state_lock:
            endpoints = tuple(self._endpoints)
        for endpoint in endpoints:
            endpoint.server_config = config
            endpoint.max_datagram_size = config.max_datagram_size

    async def start(self) -> None:
        async with self._management_lock:
            if self._started:
                raise RuntimeError("server is already started")
            self._started = True
            self._closing = False
            memory_description = self.memory.describe()
            LOGGER.info(
                "starting PLC mock config=%s endpoints=%d word_areas=%d "
                "bit_areas=%d log_mode=%s traffic=%s memory_log=%s",
                self.config.source,
                len(self._configs),
                len(memory_description["words"]),
                len(memory_description["bits"]),
                self._server_config.log_mode,
                self._server_config.traffic_log,
                self._server_config.memory_log,
                extra={
                    "event": "server_starting",
                    "config": str(self.config.source),
                    "endpoint_count": len(self._configs),
                    "word_areas": memory_description["words"],
                    "bit_areas": memory_description["bits"],
                    "log_mode": self._server_config.log_mode,
                    "traffic_log": self._server_config.traffic_log,
                    "memory_log": self._server_config.memory_log,
                },
            )
            try:
                for endpoint_config in self._configs.values():
                    await self._start_endpoint(endpoint_config)
            except Exception:
                LOGGER.exception(
                    "failed to start PLC mock",
                    extra={"event": "server_start_failed"},
                )
                await self._close_all_unlocked()
                self._started = False
                raise
            LOGGER.info(
                "PLC mock started endpoints=%s",
                self.bound_endpoints,
                extra={
                    "event": "server_started",
                    "bound_endpoints": self.bound_endpoints,
                },
            )

    async def close(self) -> None:
        async with self._management_lock:
            self._closing = True
            await self._close_all_unlocked()
            self._started = False
            self._closing = False

    async def apply_endpoint(
        self,
        name: str,
        config: EndpointConfig,
        *,
        running: bool = True,
    ) -> dict[str, Any]:
        if config.name != name:
            raise ValueError("endpoint name cannot be changed")
        async with self._management_lock:
            self._require_endpoint(name)
            prepared = self._prepare_endpoint(config)
            self._validate_binding(name, config, running=running)
            previous_config = self._configs[name]
            previous = self._find_endpoint(name)
            was_running = previous is not None
            telemetry = self._telemetry[name]
            telemetry.mark_desired(running)
            if previous is not None:
                await self._stop_instance(previous, desired=running)
            self._configs[name] = config
            try:
                if running:
                    await self._start_endpoint(config, prepared=prepared)
            except Exception as exc:
                self._configs[name] = previous_config
                rollback_error: Exception | None = None
                if was_running:
                    try:
                        await self._start_endpoint(previous_config)
                    except Exception as rollback_exc:  # pragma: no cover - rare OS failure
                        rollback_error = rollback_exc
                detail = f"cannot apply endpoint {name!r}: {exc}"
                if rollback_error is not None:
                    detail += f"; rollback also failed: {rollback_error}"
                else:
                    detail += "; previous configuration restored"
                telemetry.error(detail)
                raise RuntimeError(detail) from exc
            LOGGER.warning(
                "endpoint runtime configuration applied name=%s protocol=%s bind=%s port=%d running=%s",
                name,
                config.protocol,
                config.bind,
                config.port,
                running,
                extra={
                    "event": "endpoint_configuration_applied",
                    "endpoint": name,
                    "protocol": config.protocol,
                    "bind": config.bind,
                    "port": config.port,
                    "running": running,
                },
            )
            return self.endpoint_snapshot(name)

    async def endpoint_action(self, name: str, action: str) -> dict[str, Any]:
        action = action.strip().lower()
        async with self._management_lock:
            self._require_endpoint(name)
            if action == "start":
                self._telemetry[name].mark_desired(True)
                if self._find_endpoint(name) is None:
                    self._validate_binding(name, self._configs[name], running=True)
                    await self._start_endpoint(self._configs[name])
            elif action == "stop":
                self._telemetry[name].mark_desired(False)
                endpoint = self._find_endpoint(name)
                if endpoint is not None:
                    await self._stop_instance(endpoint, desired=False)
            elif action == "restart":
                self._telemetry[name].mark_desired(True)
                endpoint = self._find_endpoint(name)
                if endpoint is not None:
                    await self._stop_instance(endpoint, desired=True)
                self._validate_binding(name, self._configs[name], running=True)
                await self._start_endpoint(self._configs[name])
            elif action == "reset":
                startup = self._startup_configs[name]
                prepared = self._prepare_endpoint(startup)
                previous_config = self._configs[name]
                endpoint = self._find_endpoint(name)
                was_running = endpoint is not None
                if endpoint is not None:
                    await self._stop_instance(endpoint, desired=True)
                self._configs[name] = startup
                self._telemetry[name].mark_desired(True)
                try:
                    self._validate_binding(name, startup, running=True)
                    await self._start_endpoint(startup, prepared=prepared)
                except Exception as exc:
                    self._configs[name] = previous_config
                    rollback_error: Exception | None = None
                    if was_running:
                        try:
                            await self._start_endpoint(previous_config)
                        except Exception as rollback_exc:  # pragma: no cover
                            rollback_error = rollback_exc
                    detail = f"startup configuration reset failed: {exc}"
                    if rollback_error is not None:
                        detail += f"; rollback also failed: {rollback_error}"
                    else:
                        detail += "; previous configuration restored"
                    self._telemetry[name].error(detail)
                    raise RuntimeError(detail) from exc
            elif action == "reset-metrics":
                self._telemetry[name].reset_metrics()
            else:
                raise ValueError(
                    "action must be start, stop, restart, reset, or reset-metrics"
                )
            LOGGER.warning(
                "endpoint action completed name=%s action=%s",
                name,
                action,
                extra={
                    "event": "endpoint_action",
                    "endpoint": name,
                    "action": action,
                },
            )
            return self.endpoint_snapshot(name)

    def reset_all_metrics(self) -> None:
        for telemetry in self._telemetry.values():
            telemetry.reset_metrics()
        LOGGER.warning(
            "all endpoint telemetry counters reset",
            extra={"event": "telemetry_reset"},
        )

    def endpoint_snapshot(self, name: str) -> dict[str, Any]:
        self._require_endpoint(name)
        with self._state_lock:
            config = self._configs[name]
            startup = self._startup_configs[name]
            active = self._find_endpoint_unlocked(name)
        telemetry = self._telemetry[name].snapshot()
        return {
            "name": name,
            "protocol": config.protocol,
            "configured_bind": config.bind,
            "configured_port": config.port,
            "options": deepcopy(config.options),
            "faults": FaultPolicy.from_mapping(config.faults).to_mapping(),
            "startup": _config_dict(startup),
            "protocol_state": protocol_snapshot(active.plugin) if active else None,
            **telemetry,
        }

    def runtime_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            names = list(self._configs)
        endpoints = [self.endpoint_snapshot(name) for name in names]
        aggregate = aggregate_endpoint_snapshots(endpoints)
        return {
            "started": self._started,
            "closing": self._closing,
            "uptime_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "running_endpoints": sum(1 for item in endpoints if item["running"]),
            "desired_endpoints": sum(
                1 for item in endpoints if item["desired_running"]
            ),
            "healthy": all(
                (not item["desired_running"])
                or (item["running"] and not item["last_error"])
                for item in endpoints
            ),
            "metrics": aggregate["metrics"],
            "rates": aggregate["rates"],
            "history": aggregate["history"],
            "endpoints": endpoints,
        }

    def current_endpoint_configs(self) -> list[EndpointConfig]:
        with self._state_lock:
            return [deepcopy(item) for item in self._configs.values()]

    def startup_endpoint_configs(self) -> list[EndpointConfig]:
        return [deepcopy(item) for item in self._startup_configs.values()]

    async def _start_endpoint(
        self,
        endpoint_config: EndpointConfig,
        *,
        prepared: tuple[Any, FaultPolicy] | None = None,
    ) -> _EndpointProtocol:
        if self._find_endpoint(endpoint_config.name) is not None:
            raise RuntimeError(f"endpoint {endpoint_config.name!r} is already running")
        plugin, faults = prepared or self._prepare_endpoint(endpoint_config)
        telemetry = self._telemetry[endpoint_config.name]
        telemetry.mark_desired(True)
        protocol = _EndpointProtocol(
            endpoint_config,
            plugin,
            faults,
            telemetry,
            server_config=self._server_config,
        )
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda protocol=protocol: protocol,
                local_addr=(endpoint_config.bind, endpoint_config.port),
                allow_broadcast=True,
            )
        except Exception as exc:
            telemetry.mark_stopped(
                error=f"bind/start failed: {type(exc).__name__}: {exc}",
                desired=True,
            )
            raise
        with self._state_lock:
            self._transports.append(transport)
            self._endpoints.append(protocol)
        return protocol

    def _prepare_endpoint(
        self, endpoint_config: EndpointConfig
    ) -> tuple[Any, FaultPolicy]:
        plugin = load_protocol(
            endpoint_config.protocol,
            memory=self.memory,
            options=endpoint_config.options,
            plugin_paths=self.config.plugin_paths,
        )
        faults = FaultPolicy.from_mapping(endpoint_config.faults)
        return plugin, faults

    async def _stop_instance(
        self,
        endpoint: _EndpointProtocol,
        *,
        desired: bool,
    ) -> None:
        endpoint.telemetry.mark_desired(desired)
        with self._state_lock:
            if endpoint in self._endpoints:
                self._endpoints.remove(endpoint)
            if endpoint.transport in self._transports:
                self._transports.remove(endpoint.transport)
        await endpoint.shutdown()
        endpoint.telemetry.mark_stopped(desired=desired)

    async def _close_all_unlocked(self) -> None:
        with self._state_lock:
            endpoints = tuple(self._endpoints)
            self._endpoints.clear()
            self._transports.clear()
        if endpoints:
            LOGGER.info(
                "stopping PLC mock active_endpoints=%d",
                len(endpoints),
                extra={
                    "event": "server_stopping",
                    "endpoint_count": len(endpoints),
                },
            )
        for endpoint in endpoints:
            endpoint.telemetry.mark_desired(False)
        await asyncio.gather(
            *(endpoint.shutdown() for endpoint in endpoints),
            return_exceptions=True,
        )
        for endpoint in endpoints:
            endpoint.telemetry.mark_stopped(desired=False)
        if endpoints:
            LOGGER.info("PLC mock stopped", extra={"event": "server_stopped"})

    def _validate_binding(
        self,
        name: str,
        config: EndpointConfig,
        *,
        running: bool,
    ) -> None:
        if not running or config.port == 0:
            return
        with self._state_lock:
            active = tuple(self._endpoints)
        for endpoint in active:
            other = endpoint.endpoint
            if other.name == name or other.port == 0:
                continue
            if other.bind == config.bind and other.port == config.port:
                raise ValueError(
                    f"UDP binding {config.bind}:{config.port} is already used by "
                    f"endpoint {other.name!r}"
                )

    def _find_endpoint(self, name: str) -> _EndpointProtocol | None:
        with self._state_lock:
            return self._find_endpoint_unlocked(name)

    def _find_endpoint_unlocked(self, name: str) -> _EndpointProtocol | None:
        return next(
            (
                item
                for item in self._endpoints
                if item.endpoint.name == name
                and item.transport is not None
                and not item.transport.is_closing()
            ),
            None,
        )

    def _require_endpoint(self, name: str) -> None:
        if name not in self._configs:
            raise KeyError(f"unknown endpoint {name!r}")


def _config_dict(config: EndpointConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "protocol": config.protocol,
        "bind": config.bind,
        "port": config.port,
        "options": deepcopy(config.options),
        "faults": FaultPolicy.from_mapping(config.faults).to_mapping(),
    }


def _format_address(address: tuple[str, int]) -> str:
    host, port = address
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"
