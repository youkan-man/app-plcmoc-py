from __future__ import annotations

import asyncio
from pathlib import Path
import struct

from conftest import BIT_AREAS, WORD_AREAS
from plcmock.config import parse_config
from plcmock.server import UdpMockServer


class Client(asyncio.DatagramProtocol):
    def __init__(
        self,
        payload: bytes,
        target: tuple[str, int],
        future: asyncio.Future[bytes],
    ) -> None:
        self.payload = payload
        self.target = target
        self.future = future
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.transport.sendto(self.payload, self.target)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        del addr
        if not self.future.done():
            self.future.set_result(data)
        if self.transport is not None:
            self.transport.close()


async def exchange(target: tuple[str, int], payload: bytes) -> bytes:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()
    await loop.create_datagram_endpoint(
        lambda: Client(payload, target, future),
        local_addr=("127.0.0.1", 0),
    )
    return await asyncio.wait_for(future, 1.0)


def memory_config() -> dict[str, dict[str, int]]:
    return {
        "words": {name: 256 for name in WORD_AREAS},
        "bits": {name: 4096 for name in BIT_AREAS},
    }


def test_udp_server_dispatches_custom_plugin() -> None:
    async def scenario() -> None:
        config = parse_config(
            {
                "plugin_paths": ["examples"],
                "memory": {"words": {"D": 8}, "bits": {"M": 8}},
                "endpoints": [
                    {
                        "name": "ascii",
                        "protocol": "custom_ascii_protocol:AsciiDemoProtocol",
                        "bind": "127.0.0.1",
                        "port": 0,
                    }
                ],
            },
            source=Path.cwd() / "test.yml",
        )
        server = UdpMockServer(config)
        await server.start()
        try:
            response = await exchange(server.bound_endpoints["ascii"], b"PING")
            assert response == b"PONG\n"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_live_udp_mc_frames_and_other_protocols_share_memory() -> None:
    async def scenario() -> None:
        config = parse_config(
            {
                "memory": memory_config(),
                "endpoints": [
                    {
                        "name": "mc",
                        "protocol": "mc-protocol",
                        "bind": "127.0.0.1",
                        "port": 0,
                    },
                    {
                        "name": "fins",
                        "protocol": "fins-udp",
                        "bind": "127.0.0.1",
                        "port": 0,
                    },
                    {
                        "name": "modbus",
                        "protocol": "modbus-udp",
                        "bind": "127.0.0.1",
                        "port": 0,
                    },
                ],
            },
            source=Path.cwd() / "test.yml",
        )
        server = UdpMockServer(config)
        await server.start()
        try:
            endpoints = server.bound_endpoints

            # MC 1E binary: write D20 and D21.
            descriptor = (20).to_bytes(4, "little") + (0x4420).to_bytes(
                2, "little"
            )
            one_e_write = (
                bytes([0x03, 0xFF, 0x10, 0x00])
                + descriptor
                + bytes([2, 0])
                + bytes.fromhex("34 12 cd ab")
            )
            assert await exchange(endpoints["mc"], one_e_write) == bytes.fromhex(
                "83 00"
            )

            # MC 3E binary: read the same D area through the same UDP endpoint.
            route = bytes.fromhex("00 ff ff 03 00")
            payload = (
                (20).to_bytes(3, "little")
                + bytes([0xA8])
                + (2).to_bytes(2, "little")
            )
            body = bytes.fromhex("10 00 01 04 00 00") + payload
            three_e_read = (
                bytes.fromhex("50 00")
                + route
                + len(body).to_bytes(2, "little")
                + body
            )
            response = await exchange(endpoints["mc"], three_e_read)
            assert response[-6:] == bytes.fromhex("00 00 34 12 cd ab")

            # FINS/UDP DM20 reads the same canonical D memory.
            fins_header = bytes.fromhex("80 00 02 00 01 00 00 0a 00 33")
            fins_address = bytes.fromhex("82 00 14 00 00 02")
            fins_read = fins_header + bytes.fromhex("01 01") + fins_address
            response = await exchange(endpoints["fins"], fins_read)
            assert response[10:] == bytes.fromhex("01 01 00 00 12 34 ab cd")

            # Modbus holding registers 20 and 21 read the same values.
            pdu = bytes.fromhex("03 00 14 00 02")
            modbus_read = struct.pack(">HHHB", 9, 0, len(pdu) + 1, 1) + pdu
            response = await exchange(endpoints["modbus"], modbus_read)
            assert response[7:] == bytes.fromhex("03 04 12 34 ab cd")
        finally:
            await server.close()

    asyncio.run(scenario())
