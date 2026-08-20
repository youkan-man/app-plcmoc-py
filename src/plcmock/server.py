from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any

from .config import AppConfig, EndpointConfig
from .faults import FaultPolicy
from .memory import MemorySpace
from .protocols.base import DatagramContext, ProtocolResponse
from .protocols.loader import load_protocol


LOGGER = logging.getLogger("plcmock.server")


class _EndpointProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        endpoint: EndpointConfig,
        plugin: Any,
        faults: FaultPolicy,
        *,
        hex_dump: bool,
        max_datagram_size: int,
    ) -> None:
        self.endpoint = endpoint
        self.plugin = plugin
        self.faults = faults
        self.hex_dump = hex_dump
        self.max_datagram_size = max_datagram_size
        self.transport: asyncio.DatagramTransport | None = None
        self.tasks: set[asyncio.Task[None]] = set()
        self.closed = asyncio.Event()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        LOGGER.info(
            "endpoint=%s protocol=%s listening=%s",
            self.endpoint.name,
            self.plugin.protocol_name,
            transport.get_extra_info("sockname"),
        )

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > self.max_datagram_size:
            LOGGER.warning(
                "endpoint=%s rejected oversized datagram bytes=%d limit=%d source=%s",
                self.endpoint.name,
                len(data),
                self.max_datagram_size,
                addr,
            )
            return
        task = asyncio.create_task(self._handle(data, addr), name=f"plcmock:{self.endpoint.name}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def error_received(self, exc: Exception) -> None:
        LOGGER.warning("endpoint=%s UDP error: %s", self.endpoint.name, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            LOGGER.warning("endpoint=%s closed with error: %s", self.endpoint.name, exc)
        self.closed.set()

    async def _handle(self, data: bytes, remote: tuple[str, int]) -> None:
        if self.faults.should_drop():
            LOGGER.debug("endpoint=%s dropped request from %s", self.endpoint.name, remote)
            return
        transport = self.transport
        if transport is None:
            return
        local = transport.get_extra_info("sockname")
        local_address = (str(local[0]), int(local[1]))
        context = DatagramContext(self.endpoint.name, local_address, remote, time.monotonic())
        if self.hex_dump:
            LOGGER.info("endpoint=%s rx=%s bytes=%s", self.endpoint.name, remote, data.hex(" "))

        try:
            result = self.plugin.handle_datagram(data, context)
            if inspect.isawaitable(result):
                result = await result
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("endpoint=%s plugin failed", self.endpoint.name)
            return
        if result is None:
            return
        if isinstance(result, bytes):
            response = ProtocolResponse(result)
        elif isinstance(result, ProtocolResponse):
            response = result
        else:
            LOGGER.error("endpoint=%s returned unsupported response %r", self.endpoint.name, type(result))
            return

        delay = self.faults.delay_seconds() + max(0.0, response.delay_ms) / 1000.0
        if delay:
            await asyncio.sleep(delay)
        destination = response.destination or remote
        payload = self.faults.maybe_corrupt(response.payload)
        if self.hex_dump:
            LOGGER.info("endpoint=%s tx=%s bytes=%s", self.endpoint.name, destination, payload.hex(" "))
        transport.sendto(payload, destination)
        if self.faults.should_duplicate():
            transport.sendto(payload, destination)

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
                    hex_dump=self.config.server.hex_dump,
                    max_datagram_size=self.config.server.max_datagram_size,
                )
                transport, _ = await loop.create_datagram_endpoint(
                    lambda protocol=protocol: protocol,
                    local_addr=(endpoint_config.bind, endpoint_config.port),
                    allow_broadcast=True,
                )
                self._transports.append(transport)
                self._endpoints.append(protocol)
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        endpoints, self._endpoints = self._endpoints, []
        self._transports = []
        await asyncio.gather(*(endpoint.shutdown() for endpoint in endpoints), return_exceptions=True)
