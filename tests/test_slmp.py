from __future__ import annotations

import asyncio
import struct

from plcmock.protocols.base import DatagramContext
from plcmock.protocols.slmp import SlmpProtocol
from conftest import build_memory


CONTEXT = DatagramContext("test", ("127.0.0.1", 5000), ("127.0.0.1", 40000), 0.0)
OTHER_CONTEXT = DatagramContext("test", ("127.0.0.1", 5000), ("127.0.0.1", 40001), 0.0)
ROUTE_BINARY = bytes.fromhex("00 ff ff 03 00")
ROUTE_ASCII = b"00FF03FF00"


def binary_request(command: int, subcommand: int, payload: bytes = b"", *, frame4e: bool = False) -> bytes:
    body = b"\x10\x00" + command.to_bytes(2, "little") + subcommand.to_bytes(2, "little") + payload
    prefix = (
        bytes.fromhex("54 00 34 12 00 00") + ROUTE_BINARY
        if frame4e
        else bytes.fromhex("50 00") + ROUTE_BINARY
    )
    return prefix + len(body).to_bytes(2, "little") + body


def ascii_request(command: int, subcommand: int, payload: bytes = b"", *, frame4e: bool = False) -> bytes:
    body = b"0010" + f"{command:04X}{subcommand:04X}".encode() + payload
    prefix = (
        b"540012340000" + ROUTE_ASCII
        if frame4e
        else b"5000" + ROUTE_ASCII
    )
    return prefix + f"{len(body):04X}".encode() + body


def bdev(address: int, code: int, *, extended: bool = False) -> bytes:
    if extended:
        return address.to_bytes(4, "little") + code.to_bytes(2, "little")
    return address.to_bytes(3, "little") + bytes([code])


def adev(code: str, address: int, *, radix: int = 10, extended: bool = False) -> bytes:
    code_width, address_width = (4, 8) if extended else (2, 6)
    padded_code = code + "*" * (code_width - len(code))
    number = f"{address:0{address_width}{'X' if radix == 16 else 'd'}}"
    return (padded_code + number).encode()


def run(protocol: SlmpProtocol, frame: bytes, context: DatagramContext = CONTEXT) -> bytes | None:
    return asyncio.run(protocol.handle_datagram(frame, context))


def binary_payload(response: bytes) -> tuple[int, bytes]:
    prefix_size = 11 if response[:2] == b"\xD4\x00" else 7
    length = int.from_bytes(response[prefix_size:prefix_size + 2], "little")
    body = response[prefix_size + 2:]
    assert len(body) == length
    return int.from_bytes(body[:2], "little"), body[2:]


def ascii_payload(response: bytes) -> tuple[int, bytes]:
    prefix_size = 22 if response[:4] == b"D400" else 14
    length = int(response[prefix_size:prefix_size + 4], 16)
    body = response[prefix_size + 4:]
    assert len(body) == length
    return int(body[:4], 16), body[4:]


def test_binary_3e_batch_word_write_then_read() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    target = bdev(10, 0xA8) + (2).to_bytes(2, "little")
    response = run(protocol, binary_request(0x1401, 0, target + bytes.fromhex("34 12 cd ab")))
    assert response is not None and binary_payload(response) == (0, b"")
    assert memory.word("D").read_words(10, 2) == [0x1234, 0xABCD]

    response = run(protocol, binary_request(0x0401, 0, target))
    assert response is not None and binary_payload(response) == (0, bytes.fromhex("34 12 cd ab"))


def test_binary_4e_bit_units_preserve_serial() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    target = bdev(3, 0x90) + (3).to_bytes(2, "little")
    response = run(protocol, binary_request(0x1401, 1, target + bytes.fromhex("10 10"), frame4e=True))
    assert response is not None and response[:6] == bytes.fromhex("d4 00 34 12 00 00")
    assert memory.bit("M").read_bits(3, 3) == [True, False, True]
    response = run(protocol, binary_request(0x0401, 1, target, frame4e=True))
    assert response is not None and binary_payload(response) == (0, bytes.fromhex("10 10"))


def test_ascii_3e_batch_word_and_bit_access() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    word_target = adev("D", 100) + b"0002"
    response = run(protocol, ascii_request(0x1401, 0, word_target + b"19951202"))
    assert response is not None and ascii_payload(response) == (0, b"")
    assert memory.word("D").read_words(100, 2) == [0x1995, 0x1202]
    response = run(protocol, ascii_request(0x0401, 0, word_target))
    assert response is not None and ascii_payload(response) == (0, b"19951202")

    bit_target = adev("M", 100) + b"0008"
    response = run(protocol, ascii_request(0x1401, 1, bit_target + b"11001100"))
    assert response is not None and ascii_payload(response)[0] == 0
    response = run(protocol, ascii_request(0x0401, 1, bit_target))
    assert response is not None and ascii_payload(response) == (0, b"11001100")


def test_binary_bit_batch_uses_mc_bit_limit_not_word_limit() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)

    target = bdev(0, 0x90) + (1000).to_bytes(2, "little")
    response = run(protocol, binary_request(0x0401, 1, target))
    assert response is not None
    end_code, payload = binary_payload(response)
    assert end_code == 0
    assert len(payload) == 500

    too_many = bdev(0, 0x90) + (7169).to_bytes(2, "little")
    response = run(protocol, binary_request(0x0401, 1, too_many))
    assert response is not None
    assert binary_payload(response)[0] == protocol.END_INVALID_DATA


def test_ascii_4e_preserves_serial_and_route() -> None:
    memory = build_memory()
    memory.word("D").write_words(0, [0xBEEF])
    protocol = SlmpProtocol(memory)
    response = run(protocol, ascii_request(0x0401, 0, adev("D", 0) + b"0001", frame4e=True))
    assert response is not None
    assert response.startswith(b"D40012340000" + ROUTE_ASCII)
    assert ascii_payload(response) == (0, b"BEEF")


def test_extended_device_format_binary_and_ascii() -> None:
    memory = build_memory()
    memory.word("D").write_words(1234, [0xCAFE])
    protocol = SlmpProtocol(memory)

    target = bdev(1234, 0xA8, extended=True) + (1).to_bytes(2, "little")
    response = run(protocol, binary_request(0x0401, 2, target))
    assert response is not None and binary_payload(response) == (0, bytes.fromhex("fe ca"))

    target = adev("D", 1234, extended=True) + b"0001"
    response = run(protocol, ascii_request(0x0401, 2, target))
    assert response is not None and ascii_payload(response) == (0, b"CAFE")


def test_random_read_and_write_word_dword_and_bit() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    memory.word("D").write_words(0, [0x1111])
    memory.word("D").write_words(10, [0x2222, 0x3333])
    memory.bit("M").write_packed_words(100, [0x00F0])

    payload = bytes([2, 1]) + bdev(0, 0xA8) + bdev(100, 0x90) + bdev(10, 0xA8)
    response = run(protocol, binary_request(0x0403, 0, payload))
    assert response is not None
    assert binary_payload(response) == (0, struct.pack("<HHHH", 0x1111, 0x00F0, 0x2222, 0x3333))

    write_payload = (
        bytes([1, 1])
        + bdev(5, 0xA8) + struct.pack("<H", 0x4444)
        + bdev(20, 0xA8) + struct.pack("<HH", 0x5555, 0x6666)
    )
    response = run(protocol, binary_request(0x1402, 0, write_payload))
    assert response is not None and binary_payload(response)[0] == 0
    assert memory.word("D").read_words(5, 1) == [0x4444]
    assert memory.word("D").read_words(20, 2) == [0x5555, 0x6666]

    bit_payload = bytes([2]) + bdev(50, 0x90) + b"\x01" + bdev(51, 0x90) + b"\x00"
    response = run(protocol, binary_request(0x1402, 1, bit_payload))
    assert response is not None and binary_payload(response)[0] == 0
    assert memory.bit("M").read_bits(50, 2) == [True, False]


def test_random_write_enforces_mc_bit_and_word_budgets() -> None:
    protocol = SlmpProtocol(build_memory())

    # Standard-format bit random write is limited to 188 points.
    response = run(protocol, binary_request(0x1402, 1, bytes([189])))
    assert response is not None
    assert binary_payload(response)[0] == protocol.END_INVALID_DATA

    # 138 double-word operations cost 1932 budget units (138 * 14),
    # exceeding the standard-format limit of 1920.
    response = run(protocol, binary_request(0x1402, 0, bytes([0, 138])))
    assert response is not None
    assert binary_payload(response)[0] == protocol.END_INVALID_DATA


def test_ascii_random_dword_uses_high_word_first() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    memory.word("D").write_words(1500, [0x4F4E, 0x4C54])
    payload = b"0001" + adev("D", 1500)
    response = run(protocol, ascii_request(0x0403, 0, payload))
    assert response is not None and ascii_payload(response) == (0, b"4C544F4E")

    write = b"0001" + adev("D", 1600) + b"1234ABCD"
    response = run(protocol, ascii_request(0x1402, 0, write))
    assert response is not None and ascii_payload(response)[0] == 0
    assert memory.word("D").read_words(1600, 2) == [0xABCD, 0x1234]


def test_monitor_registration_is_scoped_to_udp_client() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    memory.word("D").write_words(7, [0x7777])
    plan = bytes([1, 0]) + bdev(7, 0xA8)
    response = run(protocol, binary_request(0x0801, 0, plan), CONTEXT)
    assert response is not None and binary_payload(response)[0] == 0

    response = run(protocol, binary_request(0x0802, 0), CONTEXT)
    assert response is not None and binary_payload(response) == (0, bytes.fromhex("77 77"))
    response = run(protocol, binary_request(0x0802, 0), OTHER_CONTEXT)
    assert response is not None and binary_payload(response)[0] == protocol.END_INVALID_DATA


def test_block_write_and_read() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    payload = (
        bytes([1, 1])
        + bdev(10, 0xA8) + (3).to_bytes(2, "little") + struct.pack("<HHH", 1, 2, 3)
        + bdev(32, 0x90) + (2).to_bytes(2, "little") + struct.pack("<HH", 0x00F0, 0x8001)
    )
    response = run(protocol, binary_request(0x1406, 0, payload))
    assert response is not None and binary_payload(response)[0] == 0

    descriptors = (
        bytes([1, 1])
        + bdev(10, 0xA8) + (3).to_bytes(2, "little")
        + bdev(32, 0x90) + (2).to_bytes(2, "little")
    )
    response = run(protocol, binary_request(0x0406, 0, descriptors))
    assert response is not None
    assert binary_payload(response) == (0, struct.pack("<HHHHH", 1, 2, 3, 0x00F0, 0x8001))


def test_type_name_self_test_and_clear_error() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory, {"model_name": "R08CPU", "model_code": 0x4801})
    response = run(protocol, binary_request(0x0101, 0))
    assert response is not None
    end, payload = binary_payload(response)
    assert end == 0 and payload == b"R08CPU          " + bytes.fromhex("01 48")

    response = run(protocol, ascii_request(0x0101, 0))
    assert response is not None and ascii_payload(response) == (0, b"R08CPU          4801")

    response = run(protocol, binary_request(0x0619, 0, b"\x05\x00ABCDE"))
    assert response is not None and binary_payload(response) == (0, b"\x05\x00ABCDE")
    response = run(protocol, ascii_request(0x0619, 0, b"0005ABCDE"))
    assert response is not None and ascii_payload(response) == (0, b"0005ABCDE")

    protocol.error_code = 123
    response = run(protocol, binary_request(0x1617, 0))
    assert response is not None and binary_payload(response)[0] == 0 and protocol.error_code == 0


def test_remote_control_state_machine_and_realistic_reset_no_response() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory, {"initial_state": "RUN"})
    response = run(protocol, binary_request(0x1002, 0, b"\x01\x00"))
    assert response is not None and binary_payload(response)[0] == 0 and protocol.cpu_state == "STOP"

    response = run(protocol, binary_request(0x1005, 0, b"\x01\x00"))
    assert response is not None and binary_payload(response)[0] == 0 and protocol.last_clear_mode == 2

    response = run(protocol, ascii_request(0x1001, 0, b"00010200"))
    assert response is not None and ascii_payload(response)[0] == 0
    assert protocol.cpu_state == "RUN" and protocol.last_clear_mode == 2

    response = run(protocol, binary_request(0x1003, 0, b"\x03\x00"))
    assert response is not None and binary_payload(response)[0] == 0 and protocol.cpu_state == "PAUSE"

    run(protocol, binary_request(0x1002, 0, b"\x01\x00"))
    response = run(protocol, binary_request(0x1006, 0, b"\x01\x00"))
    assert response is None and protocol.cpu_state == "RUN"


def test_malformed_write_is_fail_closed_without_partial_mutation() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    memory.word("D").write_words(0, [0xAAAA])
    target = bdev(0, 0xA8) + (2).to_bytes(2, "little")
    response = run(protocol, binary_request(0x1401, 0, target + b"\x34\x12"))
    assert response is not None and binary_payload(response)[0] != 0
    assert memory.word("D").read_words(0, 1) == [0xAAAA]


def test_unsupported_command_returns_mc_end_code() -> None:
    protocol = SlmpProtocol(build_memory())
    response = run(protocol, binary_request(0x9999, 0))
    assert response is not None and binary_payload(response)[0] == protocol.END_UNSUPPORTED


def test_slmp_model_profile_can_restrict_frames_encodings_and_commands() -> None:
    memory = build_memory()
    memory.word("D").write_words(0, [0x1234])
    protocol = SlmpProtocol(
        memory,
        {
            "accepted_frames": ["3E"],
            "accepted_encodings": ["binary"],
            "disabled_commands": ["0x0406", "0x1406"],
        },
    )

    read = bdev(0, 0xA8) + (1).to_bytes(2, "little")
    response = run(protocol, binary_request(0x0401, 0, read))
    assert response is not None and binary_payload(response) == (0, bytes.fromhex("34 12"))

    assert run(protocol, binary_request(0x0401, 0, read, frame4e=True)) is None
    assert run(protocol, ascii_request(0x0401, 0, adev("D", 0) + b"0001")) is None

    block = bytes([1, 0]) + bdev(0, 0xA8) + (1).to_bytes(2, "little")
    response = run(protocol, binary_request(0x0406, 0, block))
    assert response is not None and binary_payload(response)[0] == protocol.END_UNSUPPORTED


def test_invalid_ascii_monitoring_timer_returns_format_error() -> None:
    protocol = SlmpProtocol(build_memory())
    payload = adev("D", 0) + b"0001"
    body = b"ZZZZ04010000" + payload
    frame = b"5000" + ROUTE_ASCII + f"{len(body):04X}".encode() + body
    response = run(protocol, frame)
    assert response is not None
    assert ascii_payload(response)[0] == protocol.END_INVALID_FORMAT


def test_rejected_frame_profile_is_silent_even_when_frame_is_malformed() -> None:
    protocol = SlmpProtocol(build_memory(), {"accepted_frames": ["3E"]})
    malformed_4e = b"\x54\x00" + b"\x00" * 11
    assert run(protocol, malformed_4e) is None


def test_step_relay_device_codes_map_to_configured_sc_ss_sn_areas() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    memory.bit("SC").write_bits(1, [True])
    memory.bit("SS").write_bits(2, [True])
    memory.word("SN").write_words(3, [0x3344])

    sc = bdev(1, 0xC6) + (1).to_bytes(2, "little")
    ss = bdev(2, 0xC7) + (1).to_bytes(2, "little")
    sn = bdev(3, 0xC8) + (1).to_bytes(2, "little")
    assert binary_payload(run(protocol, binary_request(0x0401, 1, sc)))[1] == b"\x10"
    assert binary_payload(run(protocol, binary_request(0x0401, 1, ss)))[1] == b"\x10"
    assert binary_payload(run(protocol, binary_request(0x0401, 0, sn)))[1] == bytes.fromhex("44 33")


def test_binary_odd_bit_write_rejects_nonzero_unused_nibble() -> None:
    memory = build_memory()
    protocol = SlmpProtocol(memory)
    target = bdev(40, 0x90) + (3).to_bytes(2, "little")

    response = run(protocol, binary_request(0x1401, 1, target + bytes.fromhex("10 11")))
    assert response is not None
    assert binary_payload(response)[0] == protocol.END_INVALID_DATA
    assert memory.bit("M").read_bits(40, 3) == [False, False, False]
