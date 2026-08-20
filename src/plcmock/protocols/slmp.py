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


@dataclass(frozen=True, slots=True)
class SlmpFrame:
    frame_type: str
    response_prefix: bytes
    command: int
    subcommand: int
    payload: bytes


class SlmpProtocol(ProtocolPlugin):
    """Mitsubishi SLMP/MC binary 3E/4E batch read/write subset."""

    protocol_name = "slmp"

    CMD_BATCH_READ = 0x0401
    CMD_BATCH_WRITE = 0x1401

    END_OK = 0x0000
    END_INVALID_FORMAT = 0xC051
    END_RANGE = 0xC056
    END_UNSUPPORTED = 0xC059
    END_DEVICE = 0xC05B
    END_INVALID_DATA = 0xC061

    DEFAULT_DEVICES: dict[int, DeviceSpec] = {
        0x90: DeviceSpec("M", "M", "bit"),
        0x91: DeviceSpec("SM", "SM", "bit"),
        0x92: DeviceSpec("L", "L", "bit"),
        0x93: DeviceSpec("F", "F", "bit"),
        0x94: DeviceSpec("V", "V", "bit"),
        0x9C: DeviceSpec("X", "X", "bit"),
        0x9D: DeviceSpec("Y", "Y", "bit"),
        0xA0: DeviceSpec("B", "B", "bit"),
        0xA8: DeviceSpec("D", "D", "word"),
        0xA9: DeviceSpec("SD", "SD", "word"),
        0xAF: DeviceSpec("R", "R", "word"),
        0xB0: DeviceSpec("ZR", "ZR", "word"),
        0xB4: DeviceSpec("W", "W", "word"),
    }

    def __init__(self, memory, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(memory, options)
        self.max_points = _option_int(self.options, "max_points", 960, 1, 65535)
        self.devices = self._device_map(self.options.get("device_map"))

    async def handle_datagram(self, data: bytes, context: DatagramContext) -> bytes | None:
        del context
        parsed = self._parse_frame(data)
        if parsed is None:
            return None
        if isinstance(parsed, tuple):
            prefix, end_code = parsed
            return self._response(prefix, end_code)
        frame = parsed

        if frame.command not in (self.CMD_BATCH_READ, self.CMD_BATCH_WRITE):
            return self._response(frame.response_prefix, self.END_UNSUPPORTED)
        if frame.subcommand not in (0x0000, 0x0001):
            return self._response(frame.response_prefix, self.END_UNSUPPORTED)
        if len(frame.payload) < 6:
            return self._response(frame.response_prefix, self.END_INVALID_FORMAT)

        head = int.from_bytes(frame.payload[0:3], "little")
        device_code = frame.payload[3]
        points = int.from_bytes(frame.payload[4:6], "little")
        if points <= 0 or points > self.max_points:
            return self._response(frame.response_prefix, self.END_INVALID_DATA)
        device = self.devices.get(device_code)
        if device is None:
            return self._response(frame.response_prefix, self.END_DEVICE)

        try:
            if frame.command == self.CMD_BATCH_READ:
                if len(frame.payload) != 6:
                    return self._response(frame.response_prefix, self.END_INVALID_FORMAT)
                response_data = self._read(device, head, points, bit_units=bool(frame.subcommand & 1))
                return self._response(frame.response_prefix, self.END_OK, response_data)

            write_data = frame.payload[6:]
            self._write(device, head, points, write_data, bit_units=bool(frame.subcommand & 1))
            return self._response(frame.response_prefix, self.END_OK)
        except (AddressOutOfRange, UnknownArea):
            return self._response(frame.response_prefix, self.END_RANGE)
        except (InvalidMemoryValue, ValueError, struct.error):
            return self._response(frame.response_prefix, self.END_INVALID_DATA)

    def _read(self, device: DeviceSpec, head: int, points: int, *, bit_units: bool) -> bytes:
        if bit_units:
            if device.storage != "bit":
                raise ValueError("bit-unit access requires a bit device")
            return _pack_bit_units(self.memory.bit(device.area).read_bits(head, points))

        if device.storage == "word":
            values = self.memory.word(device.area).read_words(head, points)
        else:
            values = self.memory.bit(device.area).read_packed_words(head, points)
        return struct.pack(f"<{len(values)}H", *values)

    def _write(
        self,
        device: DeviceSpec,
        head: int,
        points: int,
        data: bytes,
        *,
        bit_units: bool,
    ) -> None:
        if bit_units:
            if device.storage != "bit":
                raise ValueError("bit-unit access requires a bit device")
            expected = (points + 1) // 2
            if len(data) != expected:
                raise ValueError("wrong bit payload length")
            self.memory.bit(device.area).write_bits(head, _unpack_bit_units(data, points))
            return

        expected = points * 2
        if len(data) != expected:
            raise ValueError("wrong word payload length")
        values = list(struct.unpack(f"<{points}H", data))
        if device.storage == "word":
            self.memory.word(device.area).write_words(head, values)
        else:
            self.memory.bit(device.area).write_packed_words(head, values)

    @classmethod
    def _parse_frame(cls, data: bytes) -> SlmpFrame | tuple[bytes, int] | None:
        if len(data) < 2:
            return None
        subheader = data[0:2]
        if subheader == b"\x50\x00":
            if len(data) < 9:
                return None
            prefix = b"\xD0\x00" + data[2:7]
            length_offset, body_offset = 7, 9
            frame_type = "3E"
        elif subheader == b"\x54\x00":
            if len(data) < 13:
                return None
            prefix = b"\xD4\x00" + data[2:11]
            length_offset, body_offset = 11, 13
            frame_type = "4E"
        else:
            return None

        declared_length = int.from_bytes(data[length_offset : length_offset + 2], "little")
        if declared_length < 6 or body_offset + declared_length != len(data):
            return prefix, cls.END_INVALID_FORMAT
        body = data[body_offset:]
        command = int.from_bytes(body[2:4], "little")
        subcommand = int.from_bytes(body[4:6], "little")
        return SlmpFrame(frame_type, prefix, command, subcommand, body[6:])

    @staticmethod
    def _response(prefix: bytes, end_code: int, payload: bytes = b"") -> bytes:
        body = end_code.to_bytes(2, "little") + payload
        return prefix + len(body).to_bytes(2, "little") + body

    @classmethod
    def _device_map(cls, raw: Any) -> dict[int, DeviceSpec]:
        result = dict(cls.DEFAULT_DEVICES)
        if raw is None:
            return result
        if not isinstance(raw, Mapping):
            raise ValueError("slmp.options.device_map must be a mapping")
        for raw_code, value in raw.items():
            code = _parse_int(raw_code, "device code")
            if not 0 <= code <= 0xFF or not isinstance(value, Mapping):
                raise ValueError("SLMP device override must be byte code -> mapping")
            name = str(value.get("name", f"0x{code:02X}"))
            area = str(value.get("area", name))
            storage = str(value.get("storage", "word")).lower()
            if storage not in ("word", "bit"):
                raise ValueError("SLMP device storage must be 'word' or 'bit'")
            result[code] = DeviceSpec(name, area, storage)
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
    return bits[:count]


def _option_int(options: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


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
