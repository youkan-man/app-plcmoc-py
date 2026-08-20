from __future__ import annotations

import asyncio
import inspect
from itertools import count
import logging
import time
from typing import Any

from .config import AppConfig, EndpointConfig, ServerConfig
from .diagnostics import DatagramDescription, describe_request, describe_response
from .faults import FaultPolicy
from .logging_config import TRACE, bind_log_context, format_hex
from .memory import MemorySpace
from .protocols.base import DatagramContext, ProtocolResponse
from .protocols.loader import load_protocol


LOGGER = logging.getLogger("plcmock.server")
TRAFFIC_LOGGER = logging.getLogger("plcmock.traffic")


class _EndpointProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        endpoint: EndpointConfig,
        plugin: Any,
        faults: FaultPolicy,
        *,
        server_config: ServerConfig,
    ) -> None:
        self.endpoint = endpoint
        self.plugin = plugin
        self.faults = faults
        self.server_config = server_config
        self.max_datagram_size = server_config.max_datagram_size
        self.transport: asyncio.DatagramTransport | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.closed = asyncio.Event()
        self._request_sequence = count(1)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        LOGGER.info(
            "UDP endpoint listening address=%s",
            transport.get_extra_info("sockname"),
            extra={
                "event": "endpoint_started",
                "endpoint": self.endpoint.name,
                "protocol": self.plugin.protocol_name,
                "bind": self.endpoint.bind,
                "port": self.endpoint.port,
            },
        )

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        request_id = f"{self.endpoint.name}-{next(self._request_sequence):08d}"
        remote = _format_address(addr)
        if len(data) > self.max_datagram_size:
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
        request_description = describe_request(self.plugin, data)

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
            except Exception:
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
                LOGGER.error(
                    "protocol handler returned unsupported response type=%s",
                    type(result).__name__,
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
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._log_datagram(
                direction="tx",
                data=payload,
                description=response_description,
                destination=destination,
                duration_ms=duration_ms,
            )
            transport.sendto(payload, destination)
            if duplicate:
                transport.sendto(payload, destination)
                TRAFFIC_LOGGER.warning(
                    "response duplicated by configured fault policy",
                    extra={
                        "event": "fault_duplicate",
                        "direction": "tx",
                        "destination": _format_address(destination),
                        "payload_bytes": len(payload),
                    },
                )

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
            hex_text, truncated = format_hex(data, self.server_config.max_hex_bytes)
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
            pass


class UdpMockServer:
    def __init__(self, config: AppConfig, memory: MemorySpace | None = None) -> None:
        self.config = config
        self.memory = memory or MemorySpace.from_config(config.memory)
        self._endpoints: list[_EndpointProtocol] = []
        self._transports: list[asyncio.DatagramTransport] = []

    @property
    def bound_endpoints(self) -> dict[str, tuple[str, int]]:
        result: dict[str, tuple[str, int]] = {}
        for endpoint in self._endpoints:
            if endpoint.transport is not None:
                raw = endpoint.transport.get_extra_info("sockname")
                result[endpoint.endpoint.name] = (str(raw[0]), int(raw[1]))
        return result

    async def start(self) -> None:
        if self._endpoints:
            raise RuntimeError("server is already started")
        memory_description = self.memory.describe()
        LOGGER.info(
            "starting PLC mock config=%s endpoints=%d word_areas=%d "
            "bit_areas=%d log_mode=%s traffic=%s memory_log=%s",
            self.config.source,
            len(self.config.endpoints),
            len(memory_description["words"]),
            len(memory_description["bits"]),
            self.config.server.log_mode,
            self.config.server.traffic_log,
            self.config.server.memory_log,
            extra={
                "event": "server_starting",
                "config": str(self.config.source),
                "endpoint_count": len(self.config.endpoints),
                "word_areas": memory_description["words"],
                "bit_areas": memory_description["bits"],
                "log_mode": self.config.server.log_mode,
                "traffic_log": self.config.server.traffic_log,
                "memory_log": self.config.server.memory_log,
            },
        )
        loop = asyncio.get_running_loop()
        try:
            for endpoint_config in self.config.endpoints:
                plugin = load_protocol(
                    endpoint_config.protocol,
                    memory=self.memory,
                    options=endpoint_config.options,
                    plugin_paths=self.config.plugin_paths,
                )
                faults = FaultPolicy.from_mapping(endpoint_config.faults)
                protocol = _EndpointProtocol(
                    endpoint_config,
                    plugin,
                    faults,
                    server_config=self.config.server,
                )
                transport, _ = await loop.create_datagram_endpoint(
                    lambda protocol=protocol: protocol,
                    local_addr=(endpoint_config.bind, endpoint_config.port),
                    allow_broadcast=True,
                )
                self._transports.append(transport)
                self._endpoints.append(protocol)
        except Exception:
            LOGGER.exception(
                "failed to start PLC mock",
                extra={"event": "server_start_failed"},
            )
            await self.close()
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
        endpoints, self._endpoints = self._endpoints, []
        self._transports = []
        if endpoints:
            LOGGER.info(
                "stopping PLC mock active_endpoints=%d",
                len(endpoints),
                extra={
                    "event": "server_stopping",
                    "endpoint_count": len(endpoints),
                },
            )
        await asyncio.gather(
            *(endpoint.shutdown() for endpoint in endpoints),
            return_exceptions=True,
        )
        if endpoints:
            LOGGER.info("PLC mock stopped", extra={"event": "server_stopped"})


def _format_address(address: tuple[str, int]) -> str:
    host, port = address
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"
