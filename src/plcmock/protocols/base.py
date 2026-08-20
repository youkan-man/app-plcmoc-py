from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from plcmock.memory import MemorySpace


@dataclass(frozen=True, slots=True)
class DatagramContext:
    endpoint_name: str
    local_address: tuple[str, int]
    remote_address: tuple[str, int]
    received_at: float


@dataclass(frozen=True, slots=True)
class ProtocolResponse:
    payload: bytes
    delay_ms: float = 0.0
    destination: tuple[str, int] | None = None


class ProtocolPlugin:
    """Protocol extension point.

    Subclasses receive the shared canonical memory and endpoint-specific
    options. ``handle_datagram`` may be synchronous or asynchronous; the UDP
    server normalizes both forms.
    """

    protocol_name = "custom"

    def __init__(self, memory: MemorySpace, options: Mapping[str, Any] | None = None) -> None:
        self.memory = memory
        self.options = dict(options or {})

    async def handle_datagram(
        self, data: bytes, context: DatagramContext
    ) -> ProtocolResponse | bytes | None:
        raise NotImplementedError
