import asyncio
import struct

from conftest import build_memory
from plcmock.protocols.base import DatagramContext
from plcmock.protocols.mc_1e import Mc1EProtocol
from plcmock.protocols.mc_protocol import McProtocol

CTX = DatagramContext("mc", ("127.0.0.1", 5000), ("10.0.0.3", 42000), 0.0)
CTX2 = DatagramContext("mc", ("127.0.0.1", 5000), ("10.0.0.4", 42000), 0.0)


def b_ref(head: int, code: int) -> bytes:
    return head.to_bytes(4, "little") + code.to_bytes(2, "little")


def a_ref(head: int, code: int) -> bytes:
    return f"{code:04X}{head:08X}".encode()


def b_request(command: int, payload=b"") -> bytes:
    return bytes([command, 0xFF, 0x10, 0x00]) + payload


def a_request(command: int, payload=b"") -> bytes:
    return f"{command:02X}FF0010".encode() + payload


def run(protocol, frame, context=CTX):
    response = asyncio.run(protocol.handle_datagram(frame, context))
    assert isinstance(response, bytes)
    return response


def test_1e_binary_batch_word_and_bit_read_write():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    payload = b_ref(100, 0x4420) + bytes([2, 0])
    response = run(
        protocol,
        b_request(3, payload + struct.pack("<HH", 0x1234, 0xABCD)),
    )
    assert response == bytes.fromhex("83 00")
    assert memory.word("D").read_words(100, 2) == [0x1234, 0xABCD]
    assert run(protocol, b_request(1, payload)) == bytes.fromhex("81 00 34 12 cd ab")

    bits = b_ref(50, 0x4D20) + bytes([3, 0])
    assert run(protocol, b_request(2, bits + bytes.fromhex("10 10"))) == bytes.fromhex("82 00")
    assert memory.bit("M").read_bits(50, 3) == [True, False, True]
    assert run(protocol, b_request(0, bits)) == bytes.fromhex("80 00 10 10")


def test_1e_ascii_batch_and_count_zero_means_256():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    payload = a_ref(10, 0x4420) + b"0200"
    assert run(protocol, a_request(3, payload + b"11112222")) == b"8300"
    assert run(protocol, a_request(1, payload)) == b"810011112222"

    memory.bit("M").write_bits(0, [True] * 256)
    response = run(protocol, a_request(0, a_ref(0, 0x4D20) + b"0000"))
    assert response[:4] == b"8000"
    assert response[4:] == b"1" * 256


def test_1e_random_write_bit_and_word():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    bit_payload = (
        bytes([3, 0])
        + b_ref(0x94, 0x5920)
        + b"\x01"
        + b_ref(60, 0x4D20)
        + b"\x00"
        + b_ref(0x26, 0x4220)
        + b"\x01"
    )
    assert run(protocol, b_request(4, bit_payload)) == bytes.fromhex("84 00")
    assert memory.bit("Y").read_bits(0x94, 1) == [True]
    assert memory.bit("M").read_bits(60, 1) == [False]
    assert memory.bit("B").read_bits(0x26, 1) == [True]

    word_payload = (
        b"0300"
        + a_ref(0x80, 0x5920)
        + b"7B29"
        + a_ref(0x26, 0x5720)
        + b"1234"
        + a_ref(18, 0x434E)
        + b"0050"
    )
    assert run(protocol, a_request(5, word_payload)) == b"8500"
    assert memory.bit("Y").read_packed_words(0x80, 1) == [0x7B29]
    assert memory.word("W").read_words(0x26, 1) == [0x1234]
    assert memory.word("CN").read_words(18, 1) == [0x0050]


def test_1e_monitor_registration_and_peer_isolation():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    memory.word("D").write_words(1, [0x1111])
    memory.word("D").write_words(2, [0x2222])
    run(protocol, b_request(7, bytes([1, 0]) + b_ref(1, 0x4420)), CTX)
    run(protocol, b_request(7, bytes([1, 0]) + b_ref(2, 0x4420)), CTX2)
    assert run(protocol, b_request(9), CTX) == bytes.fromhex("89 00 11 11")
    assert run(protocol, b_request(9), CTX2) == bytes.fromhex("89 00 22 22")

    memory.bit("M").write_bits(10, [True, False, True])
    registration = (
        b"0300"
        + a_ref(10, 0x4D20)
        + a_ref(11, 0x4D20)
        + a_ref(12, 0x4D20)
    )
    run(protocol, a_request(6, registration), CTX)
    assert run(protocol, a_request(8), CTX) == b"88001010"


def test_1e_ascii_bit_read_pads_an_odd_point_count():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    memory.bit("M").write_bits(20, [True, False, True])
    response = run(protocol, a_request(0, a_ref(20, 0x4D20) + b"0300"))
    assert response == b"80001010"


def test_1e_command_specific_point_limit_fails_closed():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    # Random bit write is limited to 80 points by the A-compatible 1E frame.
    payload = bytes([81, 0]) + b"".join(
        b_ref(index, 0x4D20) + b"\x01" for index in range(81)
    )
    response = run(protocol, b_request(4, payload))
    assert response == bytes.fromhex("84 10")
    assert memory.bit("M").read_bits(0, 81) == [False] * 81


def test_mc_protocol_auto_detects_1e_and_3e():
    memory = build_memory()
    protocol = McProtocol(memory)
    one_e_write = b_request(3, b_ref(5, 0x4420) + bytes([1, 0]) + bytes.fromhex("34 12"))
    assert run(protocol, one_e_write) == bytes.fromhex("83 00")

    route = bytes.fromhex("00 ff ff 03 00")
    payload = (5).to_bytes(3, "little") + bytes([0xA8]) + (1).to_bytes(2, "little")
    body = bytes.fromhex("10 00 01 04 00 00") + payload
    request = bytes.fromhex("50 00") + route + len(body).to_bytes(2, "little") + body
    response = run(protocol, request)
    assert response[-4:] == bytes.fromhex("00 00 34 12")


def test_1e_random_write_is_atomic_on_range_error():
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    memory.word("D").write_words(1, [0xAAAA])
    payload = (
        bytes([2, 0])
        + b_ref(1, 0x4420) + struct.pack("<H", 0x1111)
        + b_ref(4096, 0x4420) + struct.pack("<H", 0x2222)
    )
    response = run(protocol, b_request(5, payload))
    assert response == bytes.fromhex("85 10")
    assert memory.word("D").read_words(1, 1) == [0xAAAA]


def test_1e_pc_number_and_profile_restrictions() -> None:
    memory = build_memory()
    strict = Mc1EProtocol(
        memory,
        {
            "accept_any_1e_pc_number": False,
            "one_e_pc_number": 0x01,
        },
    )
    wrong_pc = bytes([1, 0xFF, 0x10, 0x00]) + b_ref(0, 0x4420) + bytes([1, 0])
    assert run(strict, wrong_pc) == bytes.fromhex("81 5b 10")

    disabled = Mc1EProtocol(
        memory,
        {
            "accepted_frames": ["1E"],
            "accepted_encodings": ["binary"],
            "one_e_disabled_commands": ["0x01"],
        },
    )
    read = b_request(1, b_ref(0, 0x4420) + bytes([1, 0]))
    assert run(disabled, read) == bytes.fromhex("81 10")
    assert asyncio.run(disabled.handle_datagram(a_request(1, a_ref(0, 0x4420) + b"0100"), CTX)) is None


def test_1e_ascii_odd_bit_write_uses_exact_point_count() -> None:
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    target = a_ref(20, 0x4D20) + b"0300"

    # Unlike odd-length read/monitor responses, an ASCII write request has
    # exactly one 0/1 byte per requested point and no trailing dummy byte.
    assert run(protocol, a_request(2, target + b"101")) == b"8200"
    assert memory.bit("M").read_bits(20, 3) == [True, False, True]

    assert run(protocol, a_request(2, target + b"1010")) == b"8210"
    assert memory.bit("M").read_bits(20, 3) == [True, False, True]


def test_1e_binary_odd_bit_write_rejects_nonzero_unused_nibble() -> None:
    memory = build_memory()
    protocol = Mc1EProtocol(memory)
    target = b_ref(30, 0x4D20) + bytes([3, 0])

    assert run(protocol, b_request(2, target + bytes.fromhex("10 11"))) == bytes.fromhex("82 10")
    assert memory.bit("M").read_bits(30, 3) == [False, False, False]
