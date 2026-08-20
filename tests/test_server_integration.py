import asyncio

from plcmock.config import parse_config
from plcmock.server import UdpMockServer


class Client(asyncio.DatagramProtocol):
    def __init__(self, payload: bytes, target: tuple[str, int], future: asyncio.Future[bytes]) -> None:
        self.payload = payload
        self.target = target
        self.future = future

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        transport.sendto(self.payload, self.target)  # type: ignore[attr-defined]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        del addr
        if not self.future.done():
            self.future.set_result(data)
        self.transport.close()  # type: ignore[attr-defined]


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
            source=__import__("pathlib").Path.cwd() / "test.yml",
        )
        server = UdpMockServer(config)
        await server.start()
        try:
            target = server.bound_endpoints["ascii"]
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bytes] = loop.create_future()
            await loop.create_datagram_endpoint(
                lambda: Client(b"PING", target, future), local_addr=("127.0.0.1", 0)
            )
            assert await asyncio.wait_for(future, 1.0) == b"PONG\n"
        finally:
            await server.close()

    asyncio.run(scenario())


async def exchange(target: tuple[str, int], payload: bytes) -> bytes:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()
    await loop.create_datagram_endpoint(
        lambda: Client(payload, target, future), local_addr=("127.0.0.1", 0)
    )
    return await asyncio.wait_for(future, 1.0)


def test_live_udp_cross_protocol_shared_memory() -> None:
    async def scenario() -> None:
        config = parse_config(
            {
                "memory": {
                    "words": {
                        "D": 256,
                        "W": 256,
                        "R": 256,
                        "ZR": 256,
                        "SD": 256,
                        "CIO": 256,
                        "WR": 256,
                        "HR": 256,
                        "AR": 256,
                        "EM0": 256,
                        "INPUT": 256,
                    },
                    "bits": {
                        "M": 4096,
                        "X": 4096,
                        "Y": 4096,
                        "L": 4096,
                        "F": 4096,
                        "V": 4096,
                        "B": 4096,
                        "SM": 4096,
                    },
                },
                "endpoints": [
                    {"name": "slmp", "protocol": "slmp", "bind": "127.0.0.1", "port": 0},
                    {"name": "fins", "protocol": "fins-udp", "bind": "127.0.0.1", "port": 0},
                    {"name": "modbus", "protocol": "modbus-udp", "bind": "127.0.0.1", "port": 0},
                ],
            },
            source=__import__("pathlib").Path.cwd() / "test.yml",
        )
        server = UdpMockServer(config)
        await server.start()
        try:
            endpoints = server.bound_endpoints
            fins_header = bytes.fromhex("80 00 02 00 01 00 00 0a 00 33")
            fins_address = bytes.fromhex("82 00 14 00 00 02")
            fins_write = fins_header + bytes.fromhex("01 02") + fins_address + bytes.fromhex("12 34 ab cd")
            response = await exchange(endpoints["fins"], fins_write)
            assert response[10:] == bytes.fromhex("01 02 00 00")

            route = bytes.fromhex("00 ff ff 03 00")
            slmp_payload = (20).to_bytes(3, "little") + bytes([0xA8]) + (2).to_bytes(2, "little")
            slmp_body = bytes.fromhex("10 00 01 04 00 00") + slmp_payload
            slmp_read = bytes.fromhex("50 00") + route + len(slmp_body).to_bytes(2, "little") + slmp_body
            response = await exchange(endpoints["slmp"], slmp_read)
            assert response[-6:] == bytes.fromhex("00 00 34 12 cd ab")

            import struct

            pdu = bytes.fromhex("03 00 14 00 02")
            modbus_read = struct.pack(">HHHB", 9, 0, len(pdu) + 1, 1) + pdu
            response = await exchange(endpoints["modbus"], modbus_read)
            assert response[7:] == bytes.fromhex("03 04 12 34 ab cd")
        finally:
            await server.close()

    asyncio.run(scenario())
