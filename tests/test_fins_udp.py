import asyncio

from plcmock.protocols.base import DatagramContext
from plcmock.protocols.fins_udp import FinsUdpProtocol
from conftest import build_memory


CONTEXT = DatagramContext("test", ("127.0.0.1", 9600), ("127.0.0.1", 40000), 0.0)
HEADER = bytes.fromhex("80 00 02 00 01 00 00 0a 00 22")


def run(protocol: FinsUdpProtocol, frame: bytes) -> bytes | None:
    return asyncio.run(protocol.handle_datagram(frame, CONTEXT))


def test_fins_dm_word_write_then_read_and_header_swap() -> None:
    memory = build_memory()
    protocol = FinsUdpProtocol(memory)
    address = bytes.fromhex("82 00 64 00 00 02")
    write = run(protocol, HEADER + bytes.fromhex("01 02") + address + bytes.fromhex("12 34 ab cd"))
    assert write is not None
    assert write[:10] == bytes.fromhex("c0 00 02 00 0a 00 00 01 00 22")
    assert write[10:] == bytes.fromhex("01 02 00 00")
    assert memory.word("D").read_words(100, 2) == [0x1234, 0xABCD]

    read = run(protocol, HEADER + bytes.fromhex("01 01") + address)
    assert read is not None
    assert read[10:] == bytes.fromhex("01 01 00 00 12 34 ab cd")


def test_fins_bit_access_crosses_word_boundary() -> None:
    memory = build_memory()
    protocol = FinsUdpProtocol(memory)
    address = bytes.fromhex("02 00 00 0f 00 03")
    response = run(protocol, HEADER + bytes.fromhex("01 02") + address + bytes([1, 1, 0]))
    assert response is not None and response[-2:] == b"\x00\x00"
    response = run(protocol, HEADER + bytes.fromhex("01 01") + address)
    assert response is not None and response[-3:] == bytes([1, 1, 0])


def test_fins_no_response_flag_still_applies_write() -> None:
    memory = build_memory()
    protocol = FinsUdpProtocol(memory)
    no_response_header = bytes([HEADER[0] | 1]) + HEADER[1:]
    address = bytes.fromhex("82 00 01 00 00 01")
    assert run(protocol, no_response_header + bytes.fromhex("01 02") + address + bytes.fromhex("be ef")) is None
    assert memory.word("D").read_words(1, 1) == [0xBEEF]
