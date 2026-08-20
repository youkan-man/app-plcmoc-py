from __future__ import annotations

from typing import Any, Mapping

from .base import DatagramContext, ProtocolPlugin
from .mc_1e import Mc1EProtocol
from .slmp import SlmpProtocol


class McProtocol(ProtocolPlugin):
    """Auto-detecting Mitsubishi MC endpoint for 1E, 3E, and 4E frames.

    A single UDP port accepts binary and ASCII forms. Model-specific frame and
    command restrictions are delegated to the individual handlers through the
    shared endpoint options.
    """

    protocol_name = "mc-protocol"

    def __init__(self, memory, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(memory, options)
        self.one_e = Mc1EProtocol(memory, self.options)
        self.qna = SlmpProtocol(memory, self.options)
        # Compatibility attribute used by the original composite implementation.
        self.slmp = self.qna

    async def handle_datagram(
        self, data: bytes, context: DatagramContext
    ) -> bytes | None:
        if SlmpProtocol.looks_like(data):
            return await self.qna.handle_datagram(data, context)
        if Mc1EProtocol.is_candidate(data):
            return await self.one_e.handle_datagram(data, context)
        return None
