import asyncio
import struct

from plcmock.protocols.base import DatagramContext
from plcmock.protocols.modbus_udp import ModbusUdpProtocol
from conftest import build_memory


CONTEXT = DatagramContext("test", ("127.0.0.1", 1502), ("127.0.0.1", 40000), 0.0)


def adu(function: int, payload: bytes, *, transaction: int = 7, unit: int = 1) -> bytes:
    pdu = bytes([function]) + payload
    return struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit) + pdu


def run(protocol: ModbusUdpProtocol, frame: bytes) -> bytes | None:
    return asyncio.run(protocol.handle_datagram(frame, CONTEXT))


def test_modbus_register_write_and_read_share_d_memory() -> None:
    memory = build_memory()
    protocol = ModbusUdpProtocol(memory)
    write = run(protocol, adu(0x10, struct.pack(">HHBHH", 10, 2, 4, 0x1234, 0xABCD)))
    assert write is not None and write[7:] == bytes.fromhex("10 00 0a 00 02")
    assert memory.word("D").read_words(10, 2) == [0x1234, 0xABCD]

    read = run(protocol, adu(0x03, struct.pack(">HH", 10, 2)))
    assert read is not None and read[7:] == bytes.fromhex("03 04 12 34 ab cd")


def test_modbus_coil_encoding_and_exception() -> None:
    memory = build_memory()
    protocol = ModbusUdpProtocol(memory)
    run(protocol, adu(0x0F, bytes.fromhex("00 00 00 0a 02 4d 01")))
    read = run(protocol, adu(0x01, bytes.fromhex("00 00 00 0a")))
    assert read is not None and read[7:] == bytes.fromhex("01 02 4d 01")

    unsupported = run(protocol, adu(0x44, b""))
    assert unsupported is not None and unsupported[7:] == bytes.fromhex("c4 01")


def test_modbus_broadcast_write_has_no_response() -> None:
    memory = build_memory()
    protocol = ModbusUdpProtocol(memory)
    response = run(protocol, adu(0x06, bytes.fromhex("00 02 12 34"), unit=0))
    assert response is None
    assert memory.word("D").read_words(2, 1) == [0x1234]
