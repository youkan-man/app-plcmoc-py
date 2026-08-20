from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from plcmock.memory import MemorySpace


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """Mapping between MC wire-level device identifiers and mock memory."""

    name: str
    area: str
    storage: str  # "word" or "bit"
    slmp_code: int
    ascii_code: str
    radix: int
    one_e_code: int | None = None


# Q/L-compatible SLMP device codes plus the A-compatible 1E codes that have a
# direct equivalent. Device numbers for X/Y/B/W/SB/SW/ZR are hexadecimal;
# most other device numbers are decimal in 3E/4E ASCII frames.
DEFAULT_DEVICE_SPECS: tuple[DeviceSpec, ...] = (
    DeviceSpec("SM", "SM", "bit", 0x91, "SM", 10, None),
    DeviceSpec("SD", "SD", "word", 0xA9, "SD", 10, None),
    DeviceSpec("X", "X", "bit", 0x9C, "X*", 16, 0x5820),
    DeviceSpec("Y", "Y", "bit", 0x9D, "Y*", 16, 0x5920),
    DeviceSpec("M", "M", "bit", 0x90, "M*", 10, 0x4D20),
    DeviceSpec("L", "L", "bit", 0x92, "L*", 10, None),
    DeviceSpec("F", "F", "bit", 0x93, "F*", 10, 0x4620),
    DeviceSpec("V", "V", "bit", 0x94, "V*", 10, None),
    DeviceSpec("S", "S", "bit", 0x98, "S*", 10, None),
    DeviceSpec("B", "B", "bit", 0xA0, "B*", 16, 0x4220),
    DeviceSpec("SB", "SB", "bit", 0xA1, "SB", 16, None),
    DeviceSpec("DX", "DX", "bit", 0xA2, "DX", 16, None),
    DeviceSpec("DY", "DY", "bit", 0xA3, "DY", 16, None),
    DeviceSpec("D", "D", "word", 0xA8, "D*", 10, 0x4420),
    DeviceSpec("W", "W", "word", 0xB4, "W*", 16, 0x5720),
    DeviceSpec("SW", "SW", "word", 0xB5, "SW", 16, None),
    DeviceSpec("R", "R", "word", 0xAF, "R*", 10, 0x5220),
    DeviceSpec("ZR", "ZR", "word", 0xB0, "ZR", 16, None),
    DeviceSpec("Z", "Z", "word", 0xCC, "Z*", 10, None),
    DeviceSpec("TC", "TC", "bit", 0xC0, "TC", 10, 0x5443),
    DeviceSpec("TS", "TS", "bit", 0xC1, "TS", 10, 0x5453),
    DeviceSpec("TN", "TN", "word", 0xC2, "TN", 10, 0x544E),
    DeviceSpec("CC", "CC", "bit", 0xC3, "CC", 10, 0x4343),
    DeviceSpec("CS", "CS", "bit", 0xC4, "CS", 10, 0x4353),
    DeviceSpec("CN", "CN", "word", 0xC5, "CN", 10, 0x434E),
    DeviceSpec("SC", "SC", "bit", 0xC6, "SC", 10, None),
    DeviceSpec("SS", "SS", "bit", 0xC7, "SS", 10, None),
    DeviceSpec("SN", "SN", "word", 0xC8, "SN", 10, None),
)


class DeviceCatalog:
    """Validated device lookup tables shared by MC frame variants."""

    def __init__(self, specs: Iterable[DeviceSpec]) -> None:
        self.by_slmp: dict[int, DeviceSpec] = {}
        self.by_ascii: dict[str, DeviceSpec] = {}
        self.by_one_e: dict[int, DeviceSpec] = {}
        self.by_name: dict[str, DeviceSpec] = {}
        for spec in specs:
            if spec.storage not in ("word", "bit"):
                raise ValueError(f"{spec.name}: storage must be 'word' or 'bit'")
            if spec.radix not in (10, 16):
                raise ValueError(f"{spec.name}: radix must be 10 or 16")
            if not 0 <= spec.slmp_code <= 0xFFFF:
                raise ValueError(f"{spec.name}: SLMP code must fit two bytes")
            ascii_key = normalize_ascii_code(spec.ascii_code)
            name_key = spec.name.upper()
            if spec.slmp_code in self.by_slmp:
                raise ValueError(f"duplicate SLMP device code 0x{spec.slmp_code:04X}")
            if ascii_key in self.by_ascii:
                raise ValueError(f"duplicate ASCII device code {ascii_key!r}")
            if name_key in self.by_name:
                raise ValueError(f"duplicate device name {spec.name!r}")
            if spec.one_e_code is not None and spec.one_e_code in self.by_one_e:
                raise ValueError(f"duplicate 1E device code 0x{spec.one_e_code:04X}")
            self.by_slmp[spec.slmp_code] = spec
            self.by_ascii[ascii_key] = spec
            self.by_name[name_key] = spec
            if spec.one_e_code is not None:
                self.by_one_e[spec.one_e_code] = spec

    @classmethod
    def from_options(cls, raw: Any = None) -> "DeviceCatalog":
        specs = {spec.slmp_code: spec for spec in DEFAULT_DEVICE_SPECS}
        if raw is not None:
            if not isinstance(raw, Mapping):
                raise ValueError("options.device_map must be a mapping")
            for raw_code, raw_spec in raw.items():
                code = parse_int(raw_code, "device code")
                if not 0 <= code <= 0xFFFF or not isinstance(raw_spec, Mapping):
                    raise ValueError("device override must be 16-bit code -> mapping")
                previous = specs.get(code)
                name = str(raw_spec.get("name", previous.name if previous else f"0x{code:02X}"))
                area = str(raw_spec.get("area", previous.area if previous else name))
                storage = str(
                    raw_spec.get(
                        "storage", previous.storage if previous else "word"
                    )
                ).lower()
                ascii_code = str(
                    raw_spec.get("ascii_code", previous.ascii_code if previous else name[:2])
                ).upper()
                radix = _validated_radix(raw_spec.get("radix", previous.radix if previous else 10))
                one_e_raw = raw_spec.get("one_e_code", previous.one_e_code if previous else None)
                one_e_code = None if one_e_raw is None else parse_int(one_e_raw, "1E device code")
                if one_e_code is not None and not 0 <= one_e_code <= 0xFFFF:
                    raise ValueError("1E device code must fit two bytes")
                specs[code] = DeviceSpec(
                    name=name,
                    area=area,
                    storage=storage,
                    slmp_code=code,
                    ascii_code=ascii_code,
                    radix=radix,
                    one_e_code=one_e_code,
                )
        return cls(specs.values())


def normalize_ascii_code(value: str) -> str:
    return value.upper().replace("*", "").replace(" ", "")


def parse_int(value: Any, label: str) -> int:
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


def read_bits(memory: MemorySpace, device: DeviceSpec, head: int, count: int) -> list[bool]:
    if device.storage != "bit":
        raise ValueError(f"{device.name} is not a bit device")
    return memory.bit(device.area).read_bits(head, count)


def write_bits(
    memory: MemorySpace,
    device: DeviceSpec,
    head: int,
    values: Iterable[bool | int],
) -> None:
    if device.storage != "bit":
        raise ValueError(f"{device.name} is not a bit device")
    memory.bit(device.area).write_bits(head, values)


def read_words(
    memory: MemorySpace,
    device: DeviceSpec,
    head: int,
    count: int,
    *,
    strict_bit_alignment: bool = True,
) -> list[int]:
    if device.storage == "word":
        return memory.word(device.area).read_words(head, count)
    if strict_bit_alignment and head % 16:
        raise ValueError(f"{device.name}: word-unit access requires a multiple-of-16 head")
    return memory.bit(device.area).read_packed_words(head, count)


def write_words(
    memory: MemorySpace,
    device: DeviceSpec,
    head: int,
    values: Iterable[int],
    *,
    strict_bit_alignment: bool = True,
) -> None:
    if device.storage == "word":
        memory.word(device.area).write_words(head, values)
        return
    if strict_bit_alignment and head % 16:
        raise ValueError(f"{device.name}: word-unit access requires a multiple-of-16 head")
    memory.bit(device.area).write_packed_words(head, values)


def pack_binary_bits(bits: Iterable[bool | int]) -> bytes:
    values = [bool(value) for value in bits]
    packed = bytearray()
    for offset in range(0, len(values), 2):
        high = 0x10 if values[offset] else 0
        low = 0x01 if offset + 1 < len(values) and values[offset + 1] else 0
        packed.append(high | low)
    return bytes(packed)


def unpack_binary_bits(data: bytes, count: int) -> list[int]:
    if len(data) != (count + 1) // 2:
        raise ValueError("wrong packed-bit payload length")
    values: list[int] = []
    for item in data:
        high, low = (item >> 4) & 0x0F, item & 0x0F
        if high not in (0, 1) or low not in (0, 1):
            raise ValueError("bit nibbles must be 0 or 1")
        values.extend((high, low))
    if count % 2 and data and (data[-1] & 0x0F) != 0:
        raise ValueError("unused low nibble for an odd bit count must be zero")
    return values[:count]


def _validated_radix(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in (10, 16):
        raise ValueError("device radix must be 10 or 16")
    return value
