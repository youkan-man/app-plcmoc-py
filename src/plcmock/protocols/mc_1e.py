from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any, Mapping

from plcmock.memory import AddressOutOfRange, InvalidMemoryValue, UnknownArea

from .base import DatagramContext, ProtocolPlugin
from .mc_devices import (
    DeviceCatalog,
    DeviceSpec,
    pack_binary_bits,
    read_bits,
    read_words,
    unpack_binary_bits,
    write_bits,
    write_words,
)


class _UnknownDevice(ValueError):
    pass


class _Unsupported(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Mc1EFrame:
    encoding: str
    command: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class _DeviceRef:
    device: DeviceSpec
    head: int


@dataclass(frozen=True, slots=True)
class _MonitorRegistration:
    mode: str
    refs: tuple[_DeviceRef, ...]


class Mc1EProtocol(ProtocolPlugin):
    """MELSEC-A-compatible MC protocol 1E frame over UDP.

    Supports both binary and ASCII encodings for device batch access, random
    writes, and monitor registration/execution.
    """

    protocol_name = "mc-1e"

    CMD_BATCH_READ_BIT = 0x00
    CMD_BATCH_READ_WORD = 0x01
    CMD_BATCH_WRITE_BIT = 0x02
    CMD_BATCH_WRITE_WORD = 0x03
    CMD_RANDOM_WRITE_BIT = 0x04
    CMD_RANDOM_WRITE_WORD = 0x05
    CMD_REGISTER_MONITOR_BIT = 0x06
    CMD_REGISTER_MONITOR_WORD = 0x07
    CMD_MONITOR_BIT = 0x08
    CMD_MONITOR_WORD = 0x09

    SUPPORTED_COMMANDS = frozenset(range(0x00, 0x0A))

    END_OK = 0x00
    END_ERROR = 0x10
    END_ABNORMAL = 0x5B
    ABNORMAL_PC_NUMBER = 0x10

    def __init__(self, memory, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(memory, options)
        self.max_points = _option_int(self.options, "one_e_max_points", 256, 1, 256)
        self.max_random_bit_points = _option_int(
            self.options, "one_e_max_random_bit_points", 80, 1, 256
        )
        self.max_random_word_points = _option_int(
            self.options, "one_e_max_random_word_points", 40, 1, 256
        )
        self.max_monitor_bit_points = _option_int(
            self.options, "one_e_max_monitor_bit_points", 40, 1, 256
        )
        self.max_monitor_word_points = _option_int(
            self.options, "one_e_max_monitor_word_points", 20, 1, 256
        )
        self.max_batch_word_read_bit_points = _option_int(
            self.options, "one_e_max_batch_word_read_bit_points", 128, 1, 256
        )
        self.max_batch_word_write_bit_points = _option_int(
            self.options, "one_e_max_batch_word_write_bit_points", 40, 1, 256
        )
        self.max_monitor_peers = _option_int(
            self.options, "max_monitor_peers", 1024, 1, 65535
        )
        self.strict_bit_alignment = _option_bool(
            self.options, "strict_bit_word_alignment", True
        )
        self.catalog = DeviceCatalog.from_options(self.options.get("device_map"))
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
            self.options, "one_e_enabled_commands", self.SUPPORTED_COMMANDS
        )
        disabled = _option_int_set(
            self.options, "one_e_disabled_commands", set()
        )
        self.enabled_commands = enabled - disabled
        self.accept_any_pc = _option_bool(
            self.options, "accept_any_1e_pc_number", True
        )
        self.pc_number = _option_int(
            self.options, "one_e_pc_number", 0xFF, 0, 0xFF
        )
        accepted = self.options.get("one_e_accepted_pc_numbers")
        if accepted is None:
            self.accepted_pc_numbers: set[int] | None = None
        else:
            if not isinstance(accepted, list) or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 0xFF
                for value in accepted
            ):
                raise ValueError("one_e_accepted_pc_numbers must be a list of bytes")
            self.accepted_pc_numbers = set(accepted)
        self._monitors: dict[tuple[str, int], _MonitorRegistration] = {}

    @classmethod
    def is_candidate(cls, data: bytes) -> bool:
        if len(data) >= 8 and _looks_like_ascii_command(data[:2]):
            try:
                return int(data[:2], 16) in range(0x00, 0x0A)
            except ValueError:
                return False
        return len(data) >= 4 and data[0] in range(0x00, 0x0A)

    async def handle_datagram(self, data: bytes, context: DatagramContext) -> bytes | None:
        parsed = self._parse_frame(data)
        if parsed is None:
            return None
        frame, pc_number = parsed
        if "1E" not in self.accepted_frames or frame.encoding not in self.accepted_encodings:
            return None
        pc_is_valid = (
            self.accept_any_pc
            if self.accepted_pc_numbers is None
            else pc_number in self.accepted_pc_numbers
        )
        if self.accepted_pc_numbers is None and not self.accept_any_pc:
            pc_is_valid = pc_number == self.pc_number
        if not pc_is_valid:
            return self._response(
                frame, self.END_ABNORMAL, abnormal=self.ABNORMAL_PC_NUMBER
            )
        if frame.command not in self.enabled_commands:
            return self._response(frame, self.END_ERROR)
        try:
            payload = self._dispatch(frame, context)
            return self._response(frame, self.END_OK, payload)
        except (
            _UnknownDevice,
            _Unsupported,
            AddressOutOfRange,
            UnknownArea,
            InvalidMemoryValue,
            ValueError,
            struct.error,
            UnicodeError,
        ):
            return self._response(frame, self.END_ERROR)

    def _dispatch(self, frame: Mc1EFrame, context: DatagramContext) -> bytes:
        command = frame.command
        if command in (self.CMD_BATCH_READ_BIT, self.CMD_BATCH_READ_WORD):
            return self._batch_read(frame, bit_units=command == self.CMD_BATCH_READ_BIT)
        if command in (self.CMD_BATCH_WRITE_BIT, self.CMD_BATCH_WRITE_WORD):
            self._batch_write(frame, bit_units=command == self.CMD_BATCH_WRITE_BIT)
            return b""
        if command == self.CMD_RANDOM_WRITE_BIT:
            self._random_write_bit(frame)
            return b""
        if command == self.CMD_RANDOM_WRITE_WORD:
            self._random_write_word(frame)
            return b""
        if command in (self.CMD_REGISTER_MONITOR_BIT, self.CMD_REGISTER_MONITOR_WORD):
            mode = "bit" if command == self.CMD_REGISTER_MONITOR_BIT else "word"
            refs = self._parse_monitor_refs(frame, mode)
            self._validate_monitor_refs(mode, refs)
            self._store_monitor(
                context.remote_address, _MonitorRegistration(mode, refs)
            )
            return b""
        if command in (self.CMD_MONITOR_BIT, self.CMD_MONITOR_WORD):
            if frame.payload:
                raise ValueError("1E monitor execution has no request data")
            mode = "bit" if command == self.CMD_MONITOR_BIT else "word"
            registration = self._monitors.get(context.remote_address)
            if registration is None or registration.mode != mode:
                raise _UnknownDevice("monitor data has not been registered")
            if mode == "bit":
                values = [
                    read_bits(self.memory, ref.device, ref.head, 1)[0]
                    for ref in registration.refs
                ]
                return self._encode_bits(frame.encoding, values)
            words: list[int] = []
            for ref in registration.refs:
                words.extend(
                    read_words(
                        self.memory,
                        ref.device,
                        ref.head,
                        1,
                        strict_bit_alignment=self.strict_bit_alignment,
                    )
                )
            return self._encode_words(frame.encoding, words)
        raise _Unsupported(f"unsupported 1E command 0x{command:02X}")

    def _batch_read(self, frame: Mc1EFrame, *, bit_units: bool) -> bytes:
        ref, count, data = self._parse_batch_header(frame)
        if data:
            raise ValueError("unexpected 1E batch-read data")
        if bit_units:
            values = read_bits(self.memory, ref.device, ref.head, count)
            return self._encode_bits(frame.encoding, values)
        if ref.device.storage == "bit" and count > min(
            self.max_points, self.max_batch_word_read_bit_points
        ):
            raise ValueError("too many bit-device words for a 1E batch read")
        values = read_words(
            self.memory,
            ref.device,
            ref.head,
            count,
            strict_bit_alignment=self.strict_bit_alignment,
        )
        return self._encode_words(frame.encoding, values)

    def _batch_write(self, frame: Mc1EFrame, *, bit_units: bool) -> None:
        ref, count, data = self._parse_batch_header(frame)
        if bit_units:
            if frame.encoding == "binary":
                values = unpack_binary_bits(data, count)
            else:
                # In 1E ASCII write requests, one ASCII 0/1 byte is supplied
                # for each requested point. The trailing dummy OFF byte used
                # by odd-length *responses* is not part of a write request.
                if len(data) != count or any(value not in b"01" for value in data):
                    raise ValueError("wrong ASCII bit payload")
                values = [value == ord("1") for value in data]
            write_bits(self.memory, ref.device, ref.head, values)
            return
        if ref.device.storage == "bit" and count > min(
            self.max_points, self.max_batch_word_write_bit_points
        ):
            raise ValueError("too many bit-device words for a 1E batch write")
        values = self._decode_words(frame.encoding, data, count)
        write_words(
            self.memory,
            ref.device,
            ref.head,
            values,
            strict_bit_alignment=self.strict_bit_alignment,
        )

    def _parse_batch_header(self, frame: Mc1EFrame) -> tuple[_DeviceRef, int, bytes]:
        ref, offset = self._parse_ref(frame.encoding, frame.payload, 0)
        if frame.encoding == "binary":
            if offset + 2 > len(frame.payload):
                raise ValueError("short 1E batch request")
            raw_count = frame.payload[offset]
            fixed = frame.payload[offset + 1]
            offset += 2
        else:
            if offset + 4 > len(frame.payload):
                raise ValueError("short ASCII 1E batch request")
            raw_count = _ascii_int(frame.payload[offset : offset + 2], 16)
            fixed = _ascii_int(frame.payload[offset + 2 : offset + 4], 16)
            offset += 4
        if fixed != 0:
            raise ValueError("1E fixed field must be zero")
        count = self._decode_count(raw_count)
        return ref, count, frame.payload[offset:]

    def _random_write_bit(self, frame: Mc1EFrame) -> None:
        count, offset = self._parse_count_and_fixed(frame)
        if count > min(self.max_points, self.max_random_bit_points):
            raise ValueError("too many points for a 1E random bit write")
        operations: list[tuple[_DeviceRef, int]] = []
        for _ in range(count):
            ref, offset = self._parse_ref(frame.encoding, frame.payload, offset)
            if ref.device.storage != "bit":
                raise ValueError("1E bit random write requires a bit device")
            if frame.encoding == "binary":
                if offset >= len(frame.payload):
                    raise ValueError("short 1E random-bit data")
                value = frame.payload[offset]
                offset += 1
            else:
                end = offset + 2
                if end > len(frame.payload):
                    raise ValueError("short ASCII 1E random-bit data")
                value = _ascii_int(frame.payload[offset:end], 16)
                offset = end
            if value not in (0, 1):
                raise ValueError("1E random bit value must be 0 or 1")
            operations.append((ref, value))
        if offset != len(frame.payload):
            raise ValueError("trailing 1E random-bit data")
        for ref, _ in operations:
            read_bits(self.memory, ref.device, ref.head, 1)
        for ref, value in operations:
            write_bits(self.memory, ref.device, ref.head, [value])

    def _random_write_word(self, frame: Mc1EFrame) -> None:
        count, offset = self._parse_count_and_fixed(frame)
        if count > min(self.max_points, self.max_random_word_points):
            raise ValueError("too many points for a 1E random word write")
        operations: list[tuple[_DeviceRef, int]] = []
        width = 2 if frame.encoding == "binary" else 4
        for _ in range(count):
            ref, offset = self._parse_ref(frame.encoding, frame.payload, offset)
            end = offset + width
            if end > len(frame.payload):
                raise ValueError("short 1E random-word data")
            value = self._decode_words(frame.encoding, frame.payload[offset:end], 1)[0]
            operations.append((ref, value))
            offset = end
        if offset != len(frame.payload):
            raise ValueError("trailing 1E random-word data")
        for ref, _ in operations:
            read_words(
                self.memory,
                ref.device,
                ref.head,
                1,
                strict_bit_alignment=self.strict_bit_alignment,
            )
        for ref, value in operations:
            write_words(
                self.memory,
                ref.device,
                ref.head,
                [value],
                strict_bit_alignment=self.strict_bit_alignment,
            )

    def _parse_monitor_refs(self, frame: Mc1EFrame, mode: str) -> tuple[_DeviceRef, ...]:
        count, offset = self._parse_count_and_fixed(frame)
        maximum = (
            self.max_monitor_bit_points
            if mode == "bit"
            else self.max_monitor_word_points
        )
        if count > min(self.max_points, maximum):
            raise ValueError(f"too many points for a 1E {mode} monitor")
        refs: list[_DeviceRef] = []
        for _ in range(count):
            ref, offset = self._parse_ref(frame.encoding, frame.payload, offset)
            if mode == "bit" and ref.device.storage != "bit":
                raise ValueError("1E bit monitor requires bit devices")
            if (
                mode == "word"
                and ref.device.storage == "bit"
                and self.strict_bit_alignment
                and ref.head % 16
            ):
                raise ValueError(
                    "1E word monitor bit-device head must be a multiple of 16"
                )
            refs.append(ref)
        if offset != len(frame.payload):
            raise ValueError("trailing 1E monitor-registration data")
        return tuple(refs)

    def _validate_monitor_refs(
        self, mode: str, refs: tuple[_DeviceRef, ...]
    ) -> None:
        for ref in refs:
            if mode == "bit":
                read_bits(self.memory, ref.device, ref.head, 1)
            else:
                read_words(
                    self.memory,
                    ref.device,
                    ref.head,
                    1,
                    strict_bit_alignment=self.strict_bit_alignment,
                )

    def _store_monitor(
        self, peer: tuple[str, int], registration: _MonitorRegistration
    ) -> None:
        if peer not in self._monitors and len(self._monitors) >= self.max_monitor_peers:
            oldest = next(iter(self._monitors))
            del self._monitors[oldest]
        self._monitors[peer] = registration

    def _parse_count_and_fixed(self, frame: Mc1EFrame) -> tuple[int, int]:
        if frame.encoding == "binary":
            if len(frame.payload) < 2:
                raise ValueError("short 1E point-count field")
            raw_count, fixed, offset = frame.payload[0], frame.payload[1], 2
        else:
            if len(frame.payload) < 4:
                raise ValueError("short ASCII 1E point-count field")
            raw_count = _ascii_int(frame.payload[0:2], 16)
            fixed = _ascii_int(frame.payload[2:4], 16)
            offset = 4
        if fixed != 0:
            raise ValueError("1E fixed field must be zero")
        return self._decode_count(raw_count), offset

    def _decode_count(self, raw_count: int) -> int:
        count = 256 if raw_count == 0 else raw_count
        if not 1 <= count <= self.max_points:
            raise ValueError(f"1E point count must be in 1..{self.max_points}")
        return count

    def _parse_ref(
        self, encoding: str, payload: bytes, offset: int
    ) -> tuple[_DeviceRef, int]:
        if encoding == "binary":
            end = offset + 6
            if end > len(payload):
                raise ValueError("short binary 1E device specification")
            head = int.from_bytes(payload[offset : offset + 4], "little")
            code = int.from_bytes(payload[offset + 4 : end], "little")
        else:
            end = offset + 12
            if end > len(payload):
                raise ValueError("short ASCII 1E device specification")
            code = _ascii_int(payload[offset : offset + 4], 16)
            head = _ascii_int(payload[offset + 4 : end], 16)
        device = self.catalog.by_one_e.get(code)
        if device is None:
            raise _UnknownDevice(f"unknown 1E device code 0x{code:04X}")
        return _DeviceRef(device, head), end

    @staticmethod
    def _encode_bits(encoding: str, values: list[bool]) -> bytes:
        if encoding == "binary":
            return pack_binary_bits(values)
        encoded = b"".join(b"1" if value else b"0" for value in values)
        # A-compatible 1E ASCII bit responses are byte-aligned. An odd point
        # count therefore carries one trailing dummy OFF value (ASCII ``0``).
        return encoded if len(encoded) % 2 == 0 else encoded + b"0"

    @staticmethod
    def _encode_words(encoding: str, values: list[int]) -> bytes:
        if encoding == "binary":
            return struct.pack(f"<{len(values)}H", *values) if values else b""
        return b"".join(f"{value:04X}".encode() for value in values)

    @staticmethod
    def _decode_words(encoding: str, data: bytes, count: int) -> list[int]:
        if encoding == "binary":
            if len(data) != count * 2:
                raise ValueError("wrong binary 1E word payload length")
            return list(struct.unpack(f"<{count}H", data)) if count else []
        if len(data) != count * 4:
            raise ValueError("wrong ASCII 1E word payload length")
        return [_ascii_int(data[offset : offset + 4], 16) for offset in range(0, len(data), 4)]

    @classmethod
    def _parse_frame(cls, data: bytes) -> tuple[Mc1EFrame, int] | None:
        if len(data) >= 8 and _looks_like_ascii_command(data[:2]):
            try:
                command = _ascii_int(data[0:2], 16)
                pc_number = _ascii_int(data[2:4], 16)
                _ascii_int(data[4:8], 16)  # monitoring timer
            except ValueError:
                return None
            if not 0 <= command <= 0x09:
                return None
            return Mc1EFrame("ascii", command, data[8:]), pc_number
        if len(data) < 4 or not 0 <= data[0] <= 0x09:
            return None
        return Mc1EFrame("binary", data[0], data[4:]), data[1]

    @staticmethod
    def _response(
        frame: Mc1EFrame,
        end_code: int,
        payload: bytes = b"",
        *,
        abnormal: int | None = None,
    ) -> bytes:
        response_command = frame.command | 0x80
        if frame.encoding == "binary":
            result = bytes((response_command, end_code))
            if abnormal is not None:
                result += bytes((abnormal,))
            return result + payload
        result = f"{response_command:02X}{end_code:02X}".encode()
        if abnormal is not None:
            result += f"{abnormal:02X}".encode()
        return result + payload


def _looks_like_ascii_command(data: bytes) -> bool:
    return len(data) == 2 and all(value in b"0123456789ABCDEFabcdef" for value in data)


def _ascii_int(data: bytes, radix: int) -> int:
    if not data:
        raise ValueError("missing ASCII number")
    try:
        return int(data.decode("ascii"), radix)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid ASCII number {data!r}") from exc


def _option_int(
    options: Mapping[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _option_bool(options: Mapping[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


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
        if isinstance(item, bool):
            raise ValueError(f"{name} command must be an integer")
        if isinstance(item, int):
            value = item
        elif isinstance(item, str):
            text = item.strip()
            try:
                value = int(text, 0)
            except ValueError:
                try:
                    value = int(text, 16)
                except ValueError as exc:
                    raise ValueError(f"invalid {name} command {item!r}") from exc
        else:
            raise ValueError(f"invalid {name} command {item!r}")
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{name} command values must fit one byte")
        values.add(value)
    return values


# Compatibility spelling used by earlier development snapshots.
Mc1eProtocol = Mc1EProtocol
