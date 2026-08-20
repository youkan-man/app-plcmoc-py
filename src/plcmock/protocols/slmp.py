from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any, Mapping

from plcmock.memory import AddressOutOfRange, InvalidMemoryValue, UnknownArea

from .base import DatagramContext, ProtocolPlugin


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    name: str
    area: str
    storage: str  # "word" or "bit"
    ascii_code: str
    radix: int = 10


@dataclass(frozen=True, slots=True)
class DeviceRef:
    spec: DeviceSpec
    address: int


@dataclass(frozen=True, slots=True)
class RandomPlan:
    word_refs: tuple[DeviceRef, ...]
    dword_refs: tuple[DeviceRef, ...]
    subcommand: int


@dataclass(frozen=True, slots=True)
class SlmpFrame:
    frame_type: str
    encoding: str
    response_prefix: bytes
    command: int
    subcommand: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class ParseFailure:
    encoding: str
    response_prefix: bytes
    end_code: int


class _NoResponse:
    pass


NO_RESPONSE = _NoResponse()


class SlmpProtocol(ProtocolPlugin):
    """Mitsubishi MC/SLMP UDP mock for QnA-compatible 3E/4E frames.

    Both binary and ASCII frames are accepted. The implementation focuses on
    device access, monitor registration/execution, remote control, type-name
    discovery, loopback, and error clear. Unsupported commands fail closed with
    an MC end code instead of receiving a fabricated successful response.
    """

    protocol_name = "slmp"

    CMD_READ = 0x0401
    CMD_WRITE = 0x1401
    CMD_READ_RANDOM = 0x0403
    CMD_WRITE_RANDOM = 0x1402
    CMD_ENTRY_MONITOR = 0x0801
    CMD_EXECUTE_MONITOR = 0x0802
    CMD_READ_BLOCK = 0x0406
    CMD_WRITE_BLOCK = 0x1406
    CMD_READ_TYPE_NAME = 0x0101
    CMD_REMOTE_RUN = 0x1001
    CMD_REMOTE_STOP = 0x1002
    CMD_REMOTE_PAUSE = 0x1003
    CMD_REMOTE_LATCH_CLEAR = 0x1005
    CMD_REMOTE_RESET = 0x1006
    CMD_SELF_TEST = 0x0619
    CMD_CLEAR_ERROR = 0x1617

    SUPPORTED_COMMANDS = frozenset(
        {
            CMD_READ_TYPE_NAME,
            CMD_READ,
            CMD_WRITE,
            CMD_READ_RANDOM,
            CMD_WRITE_RANDOM,
            CMD_ENTRY_MONITOR,
            CMD_EXECUTE_MONITOR,
            CMD_READ_BLOCK,
            CMD_WRITE_BLOCK,
            CMD_REMOTE_RUN,
            CMD_REMOTE_STOP,
            CMD_REMOTE_PAUSE,
            CMD_REMOTE_LATCH_CLEAR,
            CMD_REMOTE_RESET,
            CMD_SELF_TEST,
            CMD_CLEAR_ERROR,
        }
    )

    END_OK = 0x0000
    END_INVALID_FORMAT = 0xC051
    END_RANGE = 0xC056
    END_UNSUPPORTED = 0xC059
    END_DEVICE = 0xC05B
    END_INVALID_DATA = 0xC061

    DEFAULT_DEVICES: dict[int, DeviceSpec] = {
        0x91: DeviceSpec("SM", "SM", "bit", "SM", 10),
        0xA9: DeviceSpec("SD", "SD", "word", "SD", 10),
        0x9C: DeviceSpec("X", "X", "bit", "X", 16),
        0x9D: DeviceSpec("Y", "Y", "bit", "Y", 16),
        0x90: DeviceSpec("M", "M", "bit", "M", 10),
        0x92: DeviceSpec("L", "L", "bit", "L", 10),
        0x93: DeviceSpec("F", "F", "bit", "F", 10),
        0x94: DeviceSpec("V", "V", "bit", "V", 10),
        0xA0: DeviceSpec("B", "B", "bit", "B", 16),
        0xA8: DeviceSpec("D", "D", "word", "D", 10),
        0xB4: DeviceSpec("W", "W", "word", "W", 16),
        0xC1: DeviceSpec("TS", "TS", "bit", "TS", 10),
        0xC0: DeviceSpec("TC", "TC", "bit", "TC", 10),
        0xC2: DeviceSpec("TN", "TN", "word", "TN", 10),
        0xC7: DeviceSpec("STS", "SS", "bit", "SS", 10),
        0xC6: DeviceSpec("STC", "SC", "bit", "SC", 10),
        0xC8: DeviceSpec("STN", "SN", "word", "SN", 10),
        0xC4: DeviceSpec("CS", "CS", "bit", "CS", 10),
        0xC3: DeviceSpec("CC", "CC", "bit", "CC", 10),
        0xC5: DeviceSpec("CN", "CN", "word", "CN", 10),
        0xA1: DeviceSpec("SB", "SB", "bit", "SB", 16),
        0xB5: DeviceSpec("SW", "SW", "word", "SW", 16),
        0x98: DeviceSpec("S", "S", "bit", "S", 10),
        0xA2: DeviceSpec("DX", "DX", "bit", "DX", 16),
        0xA3: DeviceSpec("DY", "DY", "bit", "DY", 16),
        0xCC: DeviceSpec("Z", "Z", "word", "Z", 10),
        0xAF: DeviceSpec("R", "R", "word", "R", 10),
        0xB0: DeviceSpec("ZR", "ZR", "word", "ZR", 16),
    }

    def __init__(self, memory, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(memory, options)
        # ``max_points`` was the only limit in 0.1.x. Keep it as an alias for
        # the word-unit limit while using the protocol's larger bit-unit limits
        # by default.
        legacy_max_points = self.options.get("max_points", 960)
        self.max_word_points = _option_int(
            self.options,
            "max_word_points",
            legacy_max_points,
            1,
            65535,
        )
        self.max_bit_points_ascii = _option_int(
            self.options,
            "max_bit_points_ascii",
            3584,
            1,
            65535,
        )
        self.max_bit_points_binary = _option_int(
            self.options,
            "max_bit_points_binary",
            7168,
            1,
            65535,
        )
        self.max_random_points = _option_int(
            self.options, "max_random_points", 192, 1, 255
        )
        self.max_random_points_extended = _option_int(
            self.options, "max_random_points_extended", 96, 1, 255
        )
        self.max_random_bit_points = _option_int(
            self.options, "max_random_bit_points", 188, 1, 255
        )
        self.max_random_bit_points_extended = _option_int(
            self.options, "max_random_bit_points_extended", 94, 1, 255
        )
        self.max_random_write_budget = _option_int(
            self.options, "max_random_write_budget", 1920, 1, 65535
        )
        self.max_random_write_budget_extended = _option_int(
            self.options,
            "max_random_write_budget_extended",
            960,
            1,
            65535,
        )
        self.max_blocks = _option_int(self.options, "max_blocks", 120, 1, 255)
        self.max_blocks_extended = _option_int(
            self.options, "max_blocks_extended", 60, 1, 255
        )
        self.max_monitor_peers = _option_int(
            self.options, "max_monitor_peers", 1024, 1, 65535
        )
        self.accepted_frames = _option_string_set(
            self.options,
            "accepted_frames",
            {"1E", "3E", "4E"},
            {"1E", "3E", "4E"},
        )
        self.accepted_encodings = _option_string_set(
            self.options,
            "accepted_encodings",
            {"binary", "ascii"},
            {"binary", "ascii"},
            normalize=str.lower,
        )
        enabled = _option_int_set(
            self.options, "enabled_commands", self.SUPPORTED_COMMANDS
        )
        disabled = _option_int_set(self.options, "disabled_commands", set())
        self.enabled_commands = enabled - disabled
        self.allow_remote_control = _option_bool(
            self.options, "allow_remote_control", True
        )
        self.reset_no_response = _option_bool(
            self.options, "reset_no_response", True
        )
        self.model_name = _option_ascii(self.options, "model_name", "PLC MOCK", 16)
        self.model_code = _option_int(
            self.options, "model_code", 0, 0, 0xFFFF
        )
        self.initial_state = _option_state(self.options.get("initial_state", "RUN"))
        self.cpu_state = self.initial_state
        self.last_clear_mode = 0
        self.error_code = 0
        self.devices = self._device_map(self.options.get("device_map"))
        self.ascii_devices: dict[str, DeviceSpec] = {}
        for spec in self.devices.values():
            key = _normalize_ascii_device(spec.ascii_code)
            if key in self.ascii_devices:
                raise ValueError(f"duplicate SLMP ASCII device code {key!r}")
            self.ascii_devices[key] = spec
        self._monitor_plans: dict[tuple[str, int], RandomPlan] = {}

    async def handle_datagram(
        self, data: bytes, context: DatagramContext
    ) -> bytes | None:
        candidate = self._candidate_kind(data)
        if candidate is None:
            return None
        candidate_frame, candidate_encoding = candidate
        if (
            candidate_frame not in self.accepted_frames
            or candidate_encoding not in self.accepted_encodings
        ):
            return None
        parsed = self._parse_frame(data)
        if parsed is None:
            return None
        if isinstance(parsed, ParseFailure):
            return self._response(
                parsed.encoding, parsed.response_prefix, parsed.end_code
            )
        frame = parsed
        if (
            frame.frame_type not in self.accepted_frames
            or frame.encoding not in self.accepted_encodings
        ):
            return None

        try:
            result = self._dispatch(frame, context)
        except UnknownArea:
            return self._response(
                frame.encoding, frame.response_prefix, self.END_DEVICE
            )
        except AddressOutOfRange:
            return self._response(
                frame.encoding, frame.response_prefix, self.END_RANGE
            )
        except (InvalidMemoryValue, ValueError, struct.error, UnicodeError):
            return self._response(
                frame.encoding, frame.response_prefix, self.END_INVALID_DATA
            )

        if result is NO_RESPONSE:
            return None
        end_code, response_data = result
        return self._response(
            frame.encoding, frame.response_prefix, end_code, response_data
        )

    def _dispatch(
        self, frame: SlmpFrame, context: DatagramContext
    ) -> tuple[int, bytes] | _NoResponse:
        command = frame.command
        if command not in self.enabled_commands:
            return self.END_UNSUPPORTED, b""
        if command == self.CMD_READ:
            return self._batch_read(frame)
        if command == self.CMD_WRITE:
            return self._batch_write(frame)
        if command == self.CMD_READ_RANDOM:
            return self._random_read(frame)
        if command == self.CMD_WRITE_RANDOM:
            return self._random_write(frame)
        if command == self.CMD_ENTRY_MONITOR:
            return self._entry_monitor(frame, context)
        if command == self.CMD_EXECUTE_MONITOR:
            return self._execute_monitor(frame, context)
        if command == self.CMD_READ_BLOCK:
            return self._block_read(frame)
        if command == self.CMD_WRITE_BLOCK:
            return self._block_write(frame)
        if command == self.CMD_READ_TYPE_NAME:
            return self._read_type_name(frame)
        if command in {
            self.CMD_REMOTE_RUN,
            self.CMD_REMOTE_STOP,
            self.CMD_REMOTE_PAUSE,
            self.CMD_REMOTE_LATCH_CLEAR,
            self.CMD_REMOTE_RESET,
        }:
            return self._remote_control(frame)
        if command == self.CMD_SELF_TEST:
            return self._self_test(frame)
        if command == self.CMD_CLEAR_ERROR:
            return self._clear_error(frame)
        return self.END_UNSUPPORTED, b""

    # ------------------------------------------------------------------
    # Batch access (0401 / 1401)
    # ------------------------------------------------------------------

    def _batch_read(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if not self._valid_device_subcommand(frame.subcommand):
            return self.END_UNSUPPORTED, b""
        ref, offset = self._parse_device(frame, 0)
        points, offset = self._read_u16(frame, offset)
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""
        bit_units = bool(frame.subcommand & 1)
        if points <= 0 or points > self._batch_limit(frame.encoding, bit_units):
            return self.END_INVALID_DATA, b""

        if bit_units:
            if ref.spec.storage != "bit":
                return self.END_DEVICE, b""
            bits = self.memory.bit(ref.spec.area).read_bits(ref.address, points)
            return self.END_OK, self._encode_bit_units(frame.encoding, bits)

        values = self._read_device_words(ref, points)
        return self.END_OK, self._encode_words(frame.encoding, values)

    def _batch_write(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if not self._valid_device_subcommand(frame.subcommand):
            return self.END_UNSUPPORTED, b""
        ref, offset = self._parse_device(frame, 0)
        points, offset = self._read_u16(frame, offset)
        bit_units = bool(frame.subcommand & 1)
        if points <= 0 or points > self._batch_limit(frame.encoding, bit_units):
            return self.END_INVALID_DATA, b""

        if bit_units:
            if ref.spec.storage != "bit":
                return self.END_DEVICE, b""
            bits, offset = self._decode_bit_units(frame, offset, points)
            if offset != len(frame.payload):
                return self.END_INVALID_FORMAT, b""
            self.memory.bit(ref.spec.area).read_bits(ref.address, points)
            self.memory.bit(ref.spec.area).write_bits(ref.address, bits)
            return self.END_OK, b""

        values, offset = self._decode_words(frame, offset, points)
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""
        self._read_device_words(ref, points)
        self._write_device_words(ref, values)
        return self.END_OK, b""

    # ------------------------------------------------------------------
    # Random access and monitor (0403 / 1402 / 0801 / 0802)
    # ------------------------------------------------------------------

    def _random_read(self, frame: SlmpFrame) -> tuple[int, bytes]:
        plan, offset = self._parse_random_plan(frame)
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""
        return self.END_OK, self._encode_random_values(frame.encoding, plan)

    def _random_write(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if frame.subcommand & 0x80:
            return self.END_UNSUPPORTED, b""
        if frame.subcommand not in (0x0000, 0x0001, 0x0002, 0x0003):
            return self.END_UNSUPPORTED, b""

        if frame.subcommand & 1:
            count, offset = self._read_u8(frame, 0)
            if count <= 0 or count > self._random_bit_limit(frame.subcommand):
                return self.END_INVALID_DATA, b""
            operations: list[tuple[DeviceRef, int]] = []
            for _ in range(count):
                ref, offset = self._parse_device(frame, offset)
                if ref.spec.storage != "bit":
                    return self.END_DEVICE, b""
                value, offset = self._read_bit_value(frame, offset)
                operations.append((ref, value))
            if offset != len(frame.payload):
                return self.END_INVALID_FORMAT, b""
            for ref, _ in operations:
                self.memory.bit(ref.spec.area).read_bits(ref.address, 1)
            for ref, value in operations:
                self.memory.bit(ref.spec.area).write_bits(ref.address, [value])
            return self.END_OK, b""

        word_count, offset = self._read_u8(frame, 0)
        dword_count, offset = self._read_u8(frame, offset)
        total = word_count + dword_count
        budget = word_count * 12 + dword_count * 14
        if total <= 0 or budget > self._random_write_limit(frame.subcommand):
            return self.END_INVALID_DATA, b""

        word_ops: list[tuple[DeviceRef, list[int]]] = []
        dword_ops: list[tuple[DeviceRef, list[int]]] = []
        for _ in range(word_count):
            ref, offset = self._parse_device(frame, offset)
            values, offset = self._decode_words(frame, offset, 1)
            word_ops.append((ref, values))
        for _ in range(dword_count):
            ref, offset = self._parse_device(frame, offset)
            values, offset = self._decode_dword(frame, offset)
            dword_ops.append((ref, values))
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""

        for ref, _ in word_ops:
            self._read_device_words(ref, 1)
        for ref, _ in dword_ops:
            self._read_device_words(ref, 2)
        for ref, values in word_ops + dword_ops:
            self._write_device_words(ref, values)
        return self.END_OK, b""

    def _entry_monitor(
        self, frame: SlmpFrame, context: DatagramContext
    ) -> tuple[int, bytes]:
        plan, offset = self._parse_random_plan(frame)
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""
        # Validate every target now, as a real registration request does.
        self._read_random_values(plan)
        peer = context.remote_address
        if peer not in self._monitor_plans and len(self._monitor_plans) >= self.max_monitor_peers:
            oldest = next(iter(self._monitor_plans))
            del self._monitor_plans[oldest]
        self._monitor_plans[peer] = plan
        return self.END_OK, b""

    def _execute_monitor(
        self, frame: SlmpFrame, context: DatagramContext
    ) -> tuple[int, bytes]:
        if frame.subcommand != 0 or frame.payload:
            return self.END_INVALID_FORMAT, b""
        plan = self._monitor_plans.get(context.remote_address)
        if plan is None:
            return self.END_INVALID_DATA, b""
        return self.END_OK, self._encode_random_values(frame.encoding, plan)

    def _parse_random_plan(self, frame: SlmpFrame) -> tuple[RandomPlan, int]:
        if frame.subcommand & 0x80:
            raise ValueError("device extension specification is not supported")
        if frame.subcommand not in (0x0000, 0x0002):
            raise ValueError("invalid random-access subcommand")
        word_count, offset = self._read_u8(frame, 0)
        dword_count, offset = self._read_u8(frame, offset)
        total = word_count + dword_count
        if total <= 0 or total > self._random_limit(frame.subcommand):
            raise ValueError("invalid random-access point count")
        words: list[DeviceRef] = []
        dwords: list[DeviceRef] = []
        for _ in range(word_count):
            ref, offset = self._parse_device(frame, offset)
            words.append(ref)
        for _ in range(dword_count):
            ref, offset = self._parse_device(frame, offset)
            dwords.append(ref)
        return RandomPlan(tuple(words), tuple(dwords), frame.subcommand), offset

    def _read_random_values(self, plan: RandomPlan) -> tuple[list[int], list[list[int]]]:
        words = [self._read_device_words(ref, 1)[0] for ref in plan.word_refs]
        dwords = [self._read_device_words(ref, 2) for ref in plan.dword_refs]
        return words, dwords

    def _encode_random_values(self, encoding: str, plan: RandomPlan) -> bytes:
        words, dwords = self._read_random_values(plan)
        result = bytearray(self._encode_words(encoding, words))
        for values in dwords:
            result.extend(self._encode_dword(encoding, values))
        return bytes(result)

    # ------------------------------------------------------------------
    # Block access (0406 / 1406)
    # ------------------------------------------------------------------

    def _block_read(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if not self._valid_word_subcommand(frame.subcommand):
            return self.END_UNSUPPORTED, b""
        word_blocks, offset = self._read_u8(frame, 0)
        bit_blocks, offset = self._read_u8(frame, offset)
        if not self._valid_block_count(frame.subcommand, word_blocks, bit_blocks):
            return self.END_INVALID_DATA, b""

        descriptors: list[tuple[DeviceRef, int, str]] = []
        total_points = 0
        for kind, count in (("word", word_blocks), ("bit", bit_blocks)):
            for _ in range(count):
                ref, offset = self._parse_device(frame, offset)
                points, offset = self._read_u16(frame, offset)
                if points <= 0 or ref.spec.storage != kind:
                    return self.END_DEVICE, b""
                descriptors.append((ref, points, kind))
                total_points += points
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""
        if total_points > self.max_word_points:
            return self.END_INVALID_DATA, b""

        response = bytearray()
        for ref, points, kind in descriptors:
            if kind == "word":
                values = self.memory.word(ref.spec.area).read_words(ref.address, points)
            else:
                values = self.memory.bit(ref.spec.area).read_packed_words(ref.address, points)
            response.extend(self._encode_words(frame.encoding, values))
        return self.END_OK, bytes(response)

    def _block_write(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if not self._valid_word_subcommand(frame.subcommand):
            return self.END_UNSUPPORTED, b""
        word_blocks, offset = self._read_u8(frame, 0)
        bit_blocks, offset = self._read_u8(frame, offset)
        if not self._valid_block_count(frame.subcommand, word_blocks, bit_blocks):
            return self.END_INVALID_DATA, b""

        operations: list[tuple[DeviceRef, list[int], str]] = []
        total_points = 0
        for kind, count in (("word", word_blocks), ("bit", bit_blocks)):
            for _ in range(count):
                ref, offset = self._parse_device(frame, offset)
                points, offset = self._read_u16(frame, offset)
                if points <= 0 or ref.spec.storage != kind:
                    return self.END_DEVICE, b""
                values, offset = self._decode_words(frame, offset, points)
                operations.append((ref, values, kind))
                total_points += points
        if offset != len(frame.payload):
            return self.END_INVALID_FORMAT, b""
        descriptor_cost = 9 if frame.subcommand & 2 else 4
        if (
            total_points > self.max_word_points
            or total_points + len(operations) * descriptor_cost
            > self.max_word_points
        ):
            return self.END_INVALID_DATA, b""

        for ref, values, kind in operations:
            if kind == "word":
                self.memory.word(ref.spec.area).read_words(ref.address, len(values))
            else:
                self.memory.bit(ref.spec.area).read_bits(ref.address, len(values) * 16)
        for ref, values, kind in operations:
            if kind == "word":
                self.memory.word(ref.spec.area).write_words(ref.address, values)
            else:
                self.memory.bit(ref.spec.area).write_packed_words(ref.address, values)
        return self.END_OK, b""

    # ------------------------------------------------------------------
    # Remote control, discovery, diagnostics
    # ------------------------------------------------------------------

    def _read_type_name(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if frame.subcommand != 0 or frame.payload:
            return self.END_INVALID_FORMAT, b""
        name = self.model_name.encode("ascii").ljust(16, b" ")
        if frame.encoding == "ascii":
            return self.END_OK, name + f"{self.model_code:04X}".encode("ascii")
        return self.END_OK, name + self.model_code.to_bytes(2, "little")

    def _remote_control(
        self, frame: SlmpFrame
    ) -> tuple[int, bytes] | _NoResponse:
        if not self.allow_remote_control:
            return self.END_UNSUPPORTED, b""
        if frame.subcommand != 0:
            return self.END_UNSUPPORTED, b""

        if frame.command == self.CMD_REMOTE_RUN:
            mode, offset = self._read_u16(frame, 0)
            clear_mode, offset = self._read_u8(frame, offset)
            reserved, offset = self._read_u8(frame, offset)
            if (
                offset != len(frame.payload)
                or mode not in (1, 3)
                or clear_mode not in (0, 1, 2)
                or reserved != 0
            ):
                return self.END_INVALID_DATA, b""
            self.cpu_state = "RUN"
            self.last_clear_mode = clear_mode
            return self.END_OK, b""

        if frame.command == self.CMD_REMOTE_STOP:
            fixed, offset = self._read_u16(frame, 0)
            if offset != len(frame.payload) or fixed != 1:
                return self.END_INVALID_DATA, b""
            self.cpu_state = "STOP"
            return self.END_OK, b""

        if frame.command == self.CMD_REMOTE_PAUSE:
            mode, offset = self._read_u16(frame, 0)
            if offset != len(frame.payload) or mode not in (1, 3):
                return self.END_INVALID_DATA, b""
            self.cpu_state = "PAUSE"
            return self.END_OK, b""

        if frame.command == self.CMD_REMOTE_LATCH_CLEAR:
            fixed, offset = self._read_u16(frame, 0)
            if offset != len(frame.payload) or fixed != 1 or self.cpu_state != "STOP":
                return self.END_INVALID_DATA, b""
            self.last_clear_mode = 2
            return self.END_OK, b""

        if frame.command == self.CMD_REMOTE_RESET:
            fixed, offset = self._read_u16(frame, 0)
            if offset != len(frame.payload) or fixed != 1 or self.cpu_state != "STOP":
                return self.END_INVALID_DATA, b""
            self.cpu_state = self.initial_state
            self.last_clear_mode = 0
            self.error_code = 0
            self._monitor_plans.clear()
            return NO_RESPONSE if self.reset_no_response else (self.END_OK, b"")

        return self.END_UNSUPPORTED, b""

    def _self_test(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if frame.subcommand != 0:
            return self.END_UNSUPPORTED, b""
        count, offset = self._read_u16(frame, 0)
        data = frame.payload[offset:]
        if count <= 0 or count > 960 or len(data) != count:
            return self.END_INVALID_DATA, b""
        valid = b"0123456789ABCDEF"
        if any(value not in valid for value in data.upper()):
            return self.END_INVALID_DATA, b""
        prefix = (
            f"{count:04X}".encode("ascii")
            if frame.encoding == "ascii"
            else count.to_bytes(2, "little")
        )
        return self.END_OK, prefix + data

    def _clear_error(self, frame: SlmpFrame) -> tuple[int, bytes]:
        if frame.subcommand != 0 or frame.payload:
            return self.END_INVALID_FORMAT, b""
        self.error_code = 0
        return self.END_OK, b""

    # ------------------------------------------------------------------
    # Device/value codecs
    # ------------------------------------------------------------------

    def _parse_device(self, frame: SlmpFrame, offset: int) -> tuple[DeviceRef, int]:
        extended = bool(frame.subcommand & 2)
        payload = frame.payload
        if frame.encoding == "binary":
            address_size = 4 if extended else 3
            code_size = 2 if extended else 1
            end = offset + address_size + code_size
            if end > len(payload):
                raise ValueError("truncated device reference")
            address = int.from_bytes(payload[offset : offset + address_size], "little")
            code = int.from_bytes(payload[offset + address_size : end], "little")
            spec = self.devices.get(code)
            if spec is None:
                raise UnknownArea(f"unknown MC device code 0x{code:04X}")
            return DeviceRef(spec, address), end

        code_size = 4 if extended else 2
        address_size = 8 if extended else 6
        end = offset + code_size + address_size
        if end > len(payload):
            raise ValueError("truncated ASCII device reference")
        raw_code = payload[offset : offset + code_size].decode("ascii")
        normalized = _normalize_ascii_device(raw_code)
        spec = self.ascii_devices.get(normalized)
        if spec is None:
            raise UnknownArea(f"unknown MC ASCII device code {raw_code!r}")
        raw_address = payload[offset + code_size : end].decode("ascii").replace(" ", "0")
        if not raw_address:
            raise ValueError("empty device address")
        address = int(raw_address, spec.radix)
        return DeviceRef(spec, address), end

    def _read_u8(self, frame: SlmpFrame, offset: int) -> tuple[int, int]:
        if frame.encoding == "binary":
            if offset + 1 > len(frame.payload):
                raise ValueError("truncated byte")
            return frame.payload[offset], offset + 1
        return self._read_ascii_hex(frame.payload, offset, 2)

    def _read_u16(self, frame: SlmpFrame, offset: int) -> tuple[int, int]:
        if frame.encoding == "binary":
            if offset + 2 > len(frame.payload):
                raise ValueError("truncated word")
            return int.from_bytes(frame.payload[offset : offset + 2], "little"), offset + 2
        return self._read_ascii_hex(frame.payload, offset, 4)

    @staticmethod
    def _read_ascii_hex(payload: bytes, offset: int, width: int) -> tuple[int, int]:
        end = offset + width
        if end > len(payload):
            raise ValueError("truncated ASCII hexadecimal field")
        return int(payload[offset:end].decode("ascii"), 16), end

    def _read_bit_value(self, frame: SlmpFrame, offset: int) -> tuple[int, int]:
        extended = bool(frame.subcommand & 2)
        if frame.encoding == "binary":
            width = 2 if extended else 1
            end = offset + width
            if end > len(frame.payload):
                raise ValueError("truncated bit value")
            value = int.from_bytes(frame.payload[offset:end], "little")
        else:
            width = 4 if extended else 2
            value, end = self._read_ascii_hex(frame.payload, offset, width)
        if value not in (0, 1):
            raise ValueError("bit value must be 0 or 1")
        return value, end

    def _decode_words(
        self, frame: SlmpFrame, offset: int, count: int
    ) -> tuple[list[int], int]:
        values: list[int] = []
        if frame.encoding == "binary":
            end = offset + count * 2
            if end > len(frame.payload):
                raise ValueError("truncated word data")
            if count:
                values.extend(struct.unpack(f"<{count}H", frame.payload[offset:end]))
            return values, end
        for _ in range(count):
            value, offset = self._read_ascii_hex(frame.payload, offset, 4)
            values.append(value)
        return values, offset

    def _decode_dword(
        self, frame: SlmpFrame, offset: int
    ) -> tuple[list[int], int]:
        if frame.encoding == "binary":
            values, offset = self._decode_words(frame, offset, 2)
            return values, offset
        value, offset = self._read_ascii_hex(frame.payload, offset, 8)
        return [value & 0xFFFF, (value >> 16) & 0xFFFF], offset

    def _decode_bit_units(
        self, frame: SlmpFrame, offset: int, count: int
    ) -> tuple[list[int], int]:
        if frame.encoding == "ascii":
            end = offset + count
            if end > len(frame.payload):
                raise ValueError("truncated ASCII bit data")
            raw = frame.payload[offset:end]
            if any(value not in (0x30, 0x31) for value in raw):
                raise ValueError("ASCII bit values must be 0 or 1")
            return [value - 0x30 for value in raw], end
        byte_count = (count + 1) // 2
        end = offset + byte_count
        if end > len(frame.payload):
            raise ValueError("truncated binary bit data")
        return _unpack_bit_units(frame.payload[offset:end], count), end

    @staticmethod
    def _encode_words(encoding: str, values: list[int]) -> bytes:
        if encoding == "ascii":
            return "".join(f"{value:04X}" for value in values).encode("ascii")
        if not values:
            return b""
        return struct.pack(f"<{len(values)}H", *values)

    @staticmethod
    def _encode_dword(encoding: str, values: list[int]) -> bytes:
        if len(values) != 2:
            raise ValueError("double word requires two words")
        low, high = values
        if encoding == "ascii":
            return f"{high:04X}{low:04X}".encode("ascii")
        return struct.pack("<HH", low, high)

    @staticmethod
    def _encode_bit_units(encoding: str, bits: list[bool]) -> bytes:
        if encoding == "ascii":
            return bytes(0x31 if value else 0x30 for value in bits)
        return _pack_bit_units(bits)

    def _read_device_words(self, ref: DeviceRef, count: int) -> list[int]:
        if ref.spec.storage == "word":
            return self.memory.word(ref.spec.area).read_words(ref.address, count)
        return self.memory.bit(ref.spec.area).read_packed_words(ref.address, count)

    def _write_device_words(self, ref: DeviceRef, values: list[int]) -> None:
        if ref.spec.storage == "word":
            self.memory.word(ref.spec.area).write_words(ref.address, values)
        else:
            self.memory.bit(ref.spec.area).write_packed_words(ref.address, values)

    def _random_limit(self, subcommand: int) -> int:
        return (
            self.max_random_points_extended
            if subcommand & 2
            else self.max_random_points
        )

    def _random_bit_limit(self, subcommand: int) -> int:
        return (
            self.max_random_bit_points_extended
            if subcommand & 2
            else self.max_random_bit_points
        )

    def _random_write_limit(self, subcommand: int) -> int:
        return (
            self.max_random_write_budget_extended
            if subcommand & 2
            else self.max_random_write_budget
        )

    def _batch_limit(self, encoding: str, bit_units: bool) -> int:
        if not bit_units:
            return self.max_word_points
        return (
            self.max_bit_points_ascii
            if encoding == "ascii"
            else self.max_bit_points_binary
        )

    def _valid_block_count(self, subcommand: int, words: int, bits: int) -> bool:
        total = words + bits
        limit = self.max_blocks_extended if subcommand & 2 else self.max_blocks
        return 0 < total <= limit

    @staticmethod
    def _valid_device_subcommand(subcommand: int) -> bool:
        return not (subcommand & 0x80) and subcommand in (0, 1, 2, 3)

    @staticmethod
    def _valid_word_subcommand(subcommand: int) -> bool:
        return not (subcommand & 0x80) and subcommand in (0, 2)

    # ------------------------------------------------------------------
    # Frame parser/response builder
    # ------------------------------------------------------------------

    @classmethod
    def looks_like(cls, data: bytes) -> bool:
        return cls._candidate_kind(data) is not None

    @staticmethod
    def _candidate_kind(data: bytes) -> tuple[str, str] | None:
        if len(data) >= 2 and data[:2] == b"\x50\x00":
            return "3E", "binary"
        if len(data) >= 2 and data[:2] == b"\x54\x00":
            return "4E", "binary"
        if len(data) >= 4:
            prefix = data[:4].upper()
            if prefix == b"5000":
                return "3E", "ascii"
            if prefix == b"5400":
                return "4E", "ascii"
        return None

    @classmethod
    def _parse_frame(cls, data: bytes) -> SlmpFrame | ParseFailure | None:
        if len(data) < 2:
            return None
        if data[:2] in (b"\x50\x00", b"\x54\x00"):
            return cls._parse_binary_frame(data)
        if len(data) >= 4 and data[:4].upper() in (b"5000", b"5400"):
            return cls._parse_ascii_frame(data)
        return None

    @classmethod
    def _parse_binary_frame(cls, data: bytes) -> SlmpFrame | ParseFailure | None:
        if data[:2] == b"\x50\x00":
            if len(data) < 9:
                return None
            prefix = b"\xD0\x00" + data[2:7]
            length_offset, body_offset, frame_type = 7, 9, "3E"
        elif data[:2] == b"\x54\x00":
            if len(data) < 13:
                return None
            prefix = b"\xD4\x00" + data[2:11]
            length_offset, body_offset, frame_type = 11, 13, "4E"
        else:
            return None
        declared = int.from_bytes(data[length_offset : length_offset + 2], "little")
        if declared < 6 or body_offset + declared != len(data):
            return ParseFailure("binary", prefix, cls.END_INVALID_FORMAT)
        body = data[body_offset:]
        command = int.from_bytes(body[2:4], "little")
        subcommand = int.from_bytes(body[4:6], "little")
        return SlmpFrame(frame_type, "binary", prefix, command, subcommand, body[6:])

    @classmethod
    def _parse_ascii_frame(cls, data: bytes) -> SlmpFrame | ParseFailure | None:
        normalized = data.upper()
        if normalized[:4] == b"5000":
            if len(normalized) < 18:
                return None
            prefix = b"D000" + normalized[4:14]
            length_offset, body_offset, frame_type = 14, 18, "3E"
        elif normalized[:4] == b"5400":
            if len(normalized) < 26:
                return None
            prefix = b"D400" + normalized[4:22]
            length_offset, body_offset, frame_type = 22, 26, "4E"
        else:
            return None
        try:
            declared = int(
                normalized[length_offset : length_offset + 4].decode("ascii"), 16
            )
        except (UnicodeError, ValueError):
            return ParseFailure("ascii", prefix, cls.END_INVALID_FORMAT)
        if declared < 12 or body_offset + declared != len(normalized):
            return ParseFailure("ascii", prefix, cls.END_INVALID_FORMAT)
        body = normalized[body_offset:]
        try:
            int(body[0:4].decode("ascii"), 16)  # monitoring timer
            command = int(body[4:8].decode("ascii"), 16)
            subcommand = int(body[8:12].decode("ascii"), 16)
        except (UnicodeError, ValueError):
            return ParseFailure("ascii", prefix, cls.END_INVALID_FORMAT)
        return SlmpFrame(frame_type, "ascii", prefix, command, subcommand, body[12:])

    @staticmethod
    def _response(
        encoding: str, response_prefix: bytes, end_code: int, payload: bytes = b""
    ) -> bytes:
        if encoding == "ascii":
            body = f"{end_code:04X}".encode("ascii") + payload
            return response_prefix + f"{len(body):04X}".encode("ascii") + body
        body = end_code.to_bytes(2, "little") + payload
        return response_prefix + len(body).to_bytes(2, "little") + body

    @classmethod
    def _device_map(cls, raw: Any) -> dict[int, DeviceSpec]:
        result = dict(cls.DEFAULT_DEVICES)
        if raw is None:
            return result
        if not isinstance(raw, Mapping):
            raise ValueError("slmp.options.device_map must be a mapping")
        for raw_code, value in raw.items():
            code = _parse_int(raw_code, "device code")
            if not 0 <= code <= 0xFFFF or not isinstance(value, Mapping):
                raise ValueError("SLMP device override must be code -> mapping")
            previous = result.get(code)
            name = str(
                value.get("name", previous.name if previous else f"0x{code:04X}")
            )
            area = str(value.get("area", previous.area if previous else name))
            storage = str(
                value.get("storage", previous.storage if previous else "word")
            ).lower()
            if storage not in ("word", "bit"):
                raise ValueError("SLMP device storage must be 'word' or 'bit'")
            ascii_code = str(
                value.get(
                    "ascii_code", previous.ascii_code if previous else name
                )
            ).upper()
            if not 1 <= len(ascii_code) <= 4 or not ascii_code.isascii():
                raise ValueError("SLMP ASCII device code must be 1..4 ASCII characters")
            radix = _parse_int(
                value.get("radix", previous.radix if previous else 10),
                "device radix",
            )
            if radix not in (10, 16):
                raise ValueError("SLMP device radix must be 10 or 16")
            result[code] = DeviceSpec(name, area, storage, ascii_code, radix)
        return result


def _pack_bit_units(bits: list[bool]) -> bytes:
    packed = bytearray()
    for offset in range(0, len(bits), 2):
        first = 0x10 if bits[offset] else 0
        second = 0x01 if offset + 1 < len(bits) and bits[offset + 1] else 0
        packed.append(first | second)
    return bytes(packed)


def _unpack_bit_units(data: bytes, count: int) -> list[int]:
    bits: list[int] = []
    for value in data:
        high, low = (value >> 4) & 0x0F, value & 0x0F
        if high not in (0, 1) or low not in (0, 1):
            raise ValueError("SLMP bit nibbles must be 0 or 1")
        bits.extend((high, low))
    if count % 2 and data and (data[-1] & 0x0F) != 0:
        raise ValueError("unused low nibble for an odd bit count must be zero")
    return bits[:count]


def _normalize_ascii_device(value: str) -> str:
    return value.upper().rstrip("* ")


def _option_int(
    options: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = options.get(name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _option_bool(options: Mapping[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _option_ascii(
    options: Mapping[str, Any], name: str, default: str, maximum: int
) -> str:
    value = options.get(name, default)
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not value.isascii()
    ):
        raise ValueError(f"{name} must be non-empty ASCII with at most {maximum} characters")
    return value


def _option_state(value: Any) -> str:
    if not isinstance(value, str) or value.upper() not in {"RUN", "STOP", "PAUSE"}:
        raise ValueError("initial_state must be RUN, STOP, or PAUSE")
    return value.upper()


def _option_string_set(
    options: Mapping[str, Any],
    name: str,
    default: set[str],
    allowed: set[str],
    *,
    normalize=str.upper,
) -> set[str]:
    raw = options.get(name)
    if raw is None:
        return set(default)
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{name} must be a non-empty list")
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} entries must be non-empty strings")
        value = normalize(item.strip())
        if value not in allowed:
            raise ValueError(f"{name} contains unsupported value {item!r}")
        values.add(value)
    return values


def _option_int_set(
    options: Mapping[str, Any], name: str, default: set[int] | frozenset[int]
) -> set[int]:
    raw = options.get(name)
    if raw is None:
        return set(default)
    if not isinstance(raw, list):
        raise ValueError(f"{name} must be a list of command values")
    values: set[int] = set()
    for item in raw:
        value = _parse_command(item, name)
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"{name} command values must fit 16 bits")
        values.add(value)
    return values


def _parse_command(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} command must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0)
        except ValueError:
            try:
                return int(text, 16)
            except ValueError as exc:
                raise ValueError(f"invalid {label} command {value!r}") from exc
    raise ValueError(f"invalid {label} command {value!r}")


def _parse_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as exc:
            raise ValueError(f"invalid {label}: {value!r}") from exc
    raise ValueError(f"invalid {label}: {value!r}")
