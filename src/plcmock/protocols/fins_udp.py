from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any, Mapping

from plcmock.memory import AddressOutOfRange, InvalidMemoryValue, UnknownArea

from .base import DatagramContext, ProtocolPlugin


@dataclass(frozen=True, slots=True)
class FinsAreaSpec:
    name: str
    area: str
    unit: str  # "word" or "bit"


class FinsUdpProtocol(ProtocolPlugin):
    """OMRON FINS/UDP memory-area read/write subset (0101 and 0102)."""

    protocol_name = "fins-udp"

    END_OK = 0x0000
    END_SERVICE_UNSUPPORTED = 0x0401
    END_TOO_SHORT = 0x1002
    END_PARAMETER = 0x1101
    END_BIT_ADDRESS = 0x1103
    END_ADDRESS_RANGE = 0x1104

    DEFAULT_AREAS: dict[int, FinsAreaSpec] = {
        0x30: FinsAreaSpec("CIO bit", "CIO", "bit"),
        0x31: FinsAreaSpec("WR bit", "WR", "bit"),
        0x32: FinsAreaSpec("HR bit", "HR", "bit"),
        0x33: FinsAreaSpec("AR bit", "AR", "bit"),
        0x02: FinsAreaSpec("DM bit", "D", "bit"),
        0x20: FinsAreaSpec("EM0 bit", "EM0", "bit"),
        0xB0: FinsAreaSpec("CIO word", "CIO", "word"),
        0xB1: FinsAreaSpec("WR word", "WR", "word"),
        0xB2: FinsAreaSpec("HR word", "HR", "word"),
        0xB3: FinsAreaSpec("AR word", "AR", "word"),
        0x82: FinsAreaSpec("DM word", "D", "word"),
        0xA0: FinsAreaSpec("EM0 word", "EM0", "word"),
    }

    def __init__(self, memory, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(memory, options)
        self.max_elements = _option_int(self.options, "max_elements", 999, 1, 65535)
        self.accept_any_destination = _option_bool(
            self.options, "accept_any_destination", True
        )
        self.node = _option_int(self.options, "node", 1, 0, 255)
        self.areas = self._area_map(self.options.get("area_map"))

    async def handle_datagram(self, data: bytes, context: DatagramContext) -> bytes | None:
        del context
        if len(data) < 12:
            return None
        header = data[:10]
        if header[0] & 0x40:  # This is already a response frame; avoid reply loops.
            return None
        if not self.accept_any_destination and header[4] not in (self.node, 0xFF):
            return None

        command = data[10:12]
        payload = data[12:]
        no_response = bool(header[0] & 0x01)
        response_data = b""
        end_code = self.END_OK

        try:
            if command == b"\x01\x01":
                end_code, response_data = self._memory_read(payload)
            elif command == b"\x01\x02":
                end_code = self._memory_write(payload)
            else:
                end_code = self.END_SERVICE_UNSUPPORTED
        except (AddressOutOfRange, UnknownArea):
            end_code = self.END_ADDRESS_RANGE
            response_data = b""
        except (InvalidMemoryValue, ValueError, struct.error):
            end_code = self.END_PARAMETER
            response_data = b""

        if no_response:
            return None
        response_header = self._response_header(header)
        return response_header + command + end_code.to_bytes(2, "big") + response_data

    def _memory_read(self, payload: bytes) -> tuple[int, bytes]:
        parsed = self._parse_memory_request(payload, write=False)
        if isinstance(parsed, int):
            return parsed, b""
        area, word_address, bit_address, count, _ = parsed
        memory = self.memory.word(area.area)
        if area.unit == "word":
            if bit_address != 0:
                return self.END_BIT_ADDRESS, b""
            values = memory.read_words(word_address, count)
            return self.END_OK, struct.pack(f">{len(values)}H", *values)
        values = memory.read_bits(word_address, bit_address, count)
        return self.END_OK, bytes(int(value) for value in values)

    def _memory_write(self, payload: bytes) -> int:
        parsed = self._parse_memory_request(payload, write=True)
        if isinstance(parsed, int):
            return parsed
        area, word_address, bit_address, count, data = parsed
        memory = self.memory.word(area.area)
        if area.unit == "word":
            if bit_address != 0:
                return self.END_BIT_ADDRESS
            if len(data) != count * 2:
                return self.END_TOO_SHORT
            values = struct.unpack(f">{count}H", data)
            memory.write_words(word_address, values)
            return self.END_OK

        if len(data) != count:
            return self.END_TOO_SHORT
        if any(value not in (0, 1) for value in data):
            return self.END_PARAMETER
        memory.write_bits(word_address, bit_address, data)
        return self.END_OK

    def _parse_memory_request(
        self, payload: bytes, *, write: bool
    ) -> tuple[FinsAreaSpec, int, int, int, bytes] | int:
        if len(payload) < 6:
            return self.END_TOO_SHORT
        area = self.areas.get(payload[0])
        if area is None:
            return self.END_PARAMETER
        word_address = int.from_bytes(payload[1:3], "big")
        bit_address = payload[3]
        count = int.from_bytes(payload[4:6], "big")
        if count <= 0 or count > self.max_elements:
            return self.END_PARAMETER
        data = payload[6:]
        if not write and data:
            return self.END_PARAMETER
        return area, word_address, bit_address, count, data

    @staticmethod
    def _response_header(request: bytes) -> bytes:
        response = bytearray(request)
        response[0] |= 0x40
        destination = request[3:6]
        source = request[6:9]
        response[3:6] = source
        response[6:9] = destination
        return bytes(response)

    @classmethod
    def _area_map(cls, raw: Any) -> dict[int, FinsAreaSpec]:
        result = dict(cls.DEFAULT_AREAS)
        if raw is None:
            return result
        if not isinstance(raw, Mapping):
            raise ValueError("fins.options.area_map must be a mapping")
        for raw_code, value in raw.items():
            code = _parse_int(raw_code)
            if not 0 <= code <= 0xFF or not isinstance(value, Mapping):
                raise ValueError("FINS area override must be byte code -> mapping")
            name = str(value.get("name", f"0x{code:02X}"))
            area = str(value.get("area", name))
            unit = str(value.get("unit", "word")).lower()
            if unit not in ("word", "bit"):
                raise ValueError("FINS area unit must be 'word' or 'bit'")
            result[code] = FinsAreaSpec(name, area, unit)
        return result


def _option_int(options: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = options.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _option_bool(options: Mapping[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _parse_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("area code must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError("area code must be an integer")
