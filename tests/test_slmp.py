import asyncio

from plcmock.protocols.base import DatagramContext
from plcmock.protocols.slmp import SlmpProtocol
from conftest import build_memory


CONTEXT = DatagramContext("test", ("127.0.0.1", 5000), ("127.0.0.1", 40000), 0.0)
ROUTE = bytes.fromhex("00 ff ff 03 00")


def request(command: int, subcommand: int, payload: bytes, *, frame4e: bool = False) -> bytes:
    body = b"\x10\x00" + command.to_bytes(2, "little") + subcommand.to_bytes(2, "little") + payload
    if frame4e:
        prefix = bytes.fromhex("54 00 34 12 00 00") + ROUTE
    else:
        prefix = bytes.fromhex("50 00") + ROUTE
    return prefix + len(body).to_bytes(2, "little") + body


def run(protocol: SlmpProtocol, frame: bytes) -> bytes:
    response = asyncio.run(protocol.handle_datagram(frame, CONTEXT))
    assert isinstance(response, bytes)
    return response


def test_slmp_3e_word_write_then_read() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    payload = (10).to_bytes(3, "little") + bytes([0xA8]) + (2).to_bytes(2, "little")
    response = run(protocol, request(0x1401, 0, payload + bytes.fromhex("34 12 cd ab")))
    assert response[:2] == b"\xD0\x00"
    assert response[-2:] == b"\x00\x00"
    assert memory.word("D").read_words(10, 2) == [0x1234, 0xABCD]

    response = run(protocol, request(0x0401, 0, payload))
    assert response[-6:] == bytes.fromhex("00 00 34 12 cd ab")


def test_slmp_bit_units_and_4e_serial_are_preserved() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    payload = (3).to_bytes(3, "little") + bytes([0x90]) + (3).to_bytes(2, "little")
    write = run(protocol, request(0x1401, 1, payload + bytes.fromhex("10 10"), frame4e=True))
    assert write[:6] == bytes.fromhex("d4 00 34 12 00 00")
    assert memory.bit("M").read_bits(3, 3) == [True, False, True]
    read = run(protocol, request(0x0401, 1, payload, frame4e=True))
    assert read[-4:] == bytes.fromhex("00 00 10 10")


def test_slmp_rejects_unsupported_command_without_mutation() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    response = run(protocol, request(0x9999, 0, b""))
    assert int.from_bytes(response[-2:], "little") == protocol.END_UNSUPPORTED
