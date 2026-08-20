from __future__ import annotations

from array import array
from dataclasses import dataclass, field
import logging
from threading import RLock
from typing import Any, Iterable, Mapping

from .logging_config import TRACE, preview_values


LOGGER = logging.getLogger("plcmock.memory")


class MemoryErrorBase(ValueError):
    """Base class for deterministic PLC memory access failures."""


class UnknownArea(MemoryErrorBase):
    """Raised when a protocol maps to a memory area that does not exist."""


class AddressOutOfRange(MemoryErrorBase):
    """Raised when a read or write crosses the configured area boundary."""


class InvalidMemoryValue(MemoryErrorBase):
    """Raised when a word/bit value cannot be represented."""


def _checked_slice(start: int, count: int, size: int, area: str) -> tuple[int, int]:
    if not isinstance(start, int) or not isinstance(count, int):
        raise AddressOutOfRange(f"{area}: start and count must be integers")
    if start < 0 or count < 0 or start + count > size:
        raise AddressOutOfRange(
            f"{area}: range [{start}, {start + count}) exceeds [0, {size})"
        )
    return start, start + count


def _word(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 0xFFFF
    ):
        raise InvalidMemoryValue(
            f"word value must be an integer in 0..65535, got {value!r}"
        )
    return value


def _bit(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise InvalidMemoryValue(f"bit value must be false/true or 0/1, got {value!r}")


@dataclass(slots=True)
class WordArea:
    name: str
    size: int
    default: int = 0
    _data: array = field(init=False, repr=False)
    _lock: RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"{self.name}: size must be positive")
        self.default = _word(self.default)
        self._data = array("H", [self.default]) * self.size
        self._lock = RLock()

    def read_words(self, start: int, count: int) -> list[int]:
        begin, end = _checked_slice(start, count, self.size, self.name)
        with self._lock:
            values = self._data[begin:end].tolist()
        _log_memory(
            TRACE,
            operation="read",
            storage="word",
            area=self.name,
            address=start,
            count=count,
            values=values,
        )
        return values

    def write_words(self, start: int, values: Iterable[int]) -> None:
        normalized = array("H", (_word(value) for value in values))
        begin, end = _checked_slice(start, len(normalized), self.size, self.name)
        with self._lock:
            self._data[begin:end] = normalized
        _log_memory(
            logging.DEBUG,
            operation="write",
            storage="word",
            area=self.name,
            address=start,
            count=len(normalized),
            values=normalized,
        )

    def read_bits(self, word_address: int, bit_address: int, count: int) -> list[bool]:
        if not 0 <= bit_address <= 15:
            raise AddressOutOfRange(f"{self.name}: bit address must be in 0..15")
        start_bit = word_address * 16 + bit_address
        total_bits = self.size * 16
        begin, end = _checked_slice(start_bit, count, total_bits, self.name)
        with self._lock:
            values = [
                bool((self._data[index // 16] >> (index % 16)) & 1)
                for index in range(begin, end)
            ]
        _log_memory(
            TRACE,
            operation="read",
            storage="word-bit",
            area=self.name,
            address=word_address,
            bit_address=bit_address,
            count=count,
            values=values,
        )
        return values

    def write_bits(
        self,
        word_address: int,
        bit_address: int,
        values: Iterable[bool | int],
    ) -> None:
        normalized = [_bit(value) for value in values]
        if not 0 <= bit_address <= 15:
            raise AddressOutOfRange(f"{self.name}: bit address must be in 0..15")
        start_bit = word_address * 16 + bit_address
        total_bits = self.size * 16
        begin, end = _checked_slice(start_bit, len(normalized), total_bits, self.name)
        with self._lock:
            for index, value in zip(range(begin, end), normalized, strict=True):
                word_index, bit_index = divmod(index, 16)
                mask = 1 << bit_index
                current = self._data[word_index]
                self._data[word_index] = (
                    (current | mask) if value else (current & ~mask)
                )
        _log_memory(
            logging.DEBUG,
            operation="write",
            storage="word-bit",
            area=self.name,
            address=word_address,
            bit_address=bit_address,
            count=len(normalized),
            values=normalized,
        )


@dataclass(slots=True)
class BitArea:
    name: str
    size: int
    default: bool = False
    _data: bytearray = field(init=False, repr=False)
    _lock: RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"{self.name}: size must be positive")
        initial = _bit(self.default)
        self._data = bytearray([initial]) * self.size
        self._lock = RLock()

    def read_bits(self, start: int, count: int) -> list[bool]:
        begin, end = _checked_slice(start, count, self.size, self.name)
        with self._lock:
            values = [bool(value) for value in self._data[begin:end]]
        _log_memory(
            TRACE,
            operation="read",
            storage="bit",
            area=self.name,
            address=start,
            count=count,
            values=values,
        )
        return values

    def write_bits(self, start: int, values: Iterable[bool | int]) -> None:
        normalized = bytearray(_bit(value) for value in values)
        begin, end = _checked_slice(start, len(normalized), self.size, self.name)
        with self._lock:
            self._data[begin:end] = normalized
        _log_memory(
            logging.DEBUG,
            operation="write",
            storage="bit",
            area=self.name,
            address=start,
            count=len(normalized),
            values=normalized,
        )

    def read_packed_words(self, start_bit: int, count: int) -> list[int]:
        bits = self.read_bits(start_bit, count * 16)
        words: list[int] = []
        for offset in range(0, len(bits), 16):
            value = 0
            for bit_index, enabled in enumerate(bits[offset : offset + 16]):
                if enabled:
                    value |= 1 << bit_index
            words.append(value)
        return words

    def write_packed_words(self, start_bit: int, values: Iterable[int]) -> None:
        bits: list[int] = []
        for value in values:
            normalized = _word(value)
            bits.extend((normalized >> bit_index) & 1 for bit_index in range(16))
        self.write_bits(start_bit, bits)


class MemorySpace:
    """Canonical memory shared by every configured protocol endpoint."""

    def __init__(self) -> None:
        self._words: dict[str, WordArea] = {}
        self._bits: dict[str, BitArea] = {}

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "MemorySpace":
        space = cls()
        config = config or {}
        words = config.get("words", {})
        bits = config.get("bits", {})
        if not isinstance(words, Mapping) or not isinstance(bits, Mapping):
            raise ValueError("memory.words and memory.bits must be mappings")

        for name, raw in words.items():
            settings = _area_settings(raw, f"memory.words.{name}")
            area = WordArea(str(name), settings["size"], settings.get("default", 0))
            _initialize_area(area, settings.get("values", {}), word=True)
            space.add_word_area(area)

        for name, raw in bits.items():
            settings = _area_settings(raw, f"memory.bits.{name}")
            area = BitArea(str(name), settings["size"], settings.get("default", False))
            _initialize_area(area, settings.get("values", {}), word=False)
            space.add_bit_area(area)

        return space

    def add_word_area(self, area: WordArea) -> None:
        if area.name in self._words or area.name in self._bits:
            raise ValueError(f"duplicate memory area {area.name!r}")
        self._words[area.name] = area

    def add_bit_area(self, area: BitArea) -> None:
        if area.name in self._words or area.name in self._bits:
            raise ValueError(f"duplicate memory area {area.name!r}")
        self._bits[area.name] = area

    def word(self, name: str) -> WordArea:
        try:
            return self._words[name]
        except KeyError as exc:
            raise UnknownArea(f"unknown word area {name!r}") from exc

    def bit(self, name: str) -> BitArea:
        try:
            return self._bits[name]
        except KeyError as exc:
            raise UnknownArea(f"unknown bit area {name!r}") from exc

    def describe(self) -> dict[str, dict[str, int]]:
        return {
            "words": {name: area.size for name, area in self._words.items()},
            "bits": {name: area.size for name, area in self._bits.items()},
        }


def _log_memory(
    level: int,
    *,
    operation: str,
    storage: str,
    area: str,
    address: int,
    count: int,
    values: Iterable[Any],
    bit_address: int | None = None,
) -> None:
    if not LOGGER.isEnabledFor(level):
        return
    preview, truncated = preview_values(values)
    location = f"{address}.{bit_address}" if bit_address is not None else str(address)
    suffix = " ..." if truncated else ""
    LOGGER.log(
        level,
        "%s %s area=%s address=%s count=%d values=%s%s",
        operation,
        storage,
        area,
        location,
        count,
        preview,
        suffix,
        extra={
            "event": "memory_access",
            "memory_operation": operation,
            "memory_storage": storage,
            "memory_area": area,
            "address": address,
            "bit_address": bit_address,
            "count": count,
            "values": preview,
            "values_truncated": truncated,
        },
    )


def _area_settings(raw: Any, where: str) -> dict[str, Any]:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return {"size": raw}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} must be an integer size or a mapping")
    settings = dict(raw)
    size = settings.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{where}.size must be a positive integer")
    return settings


def _initialize_area(area: WordArea | BitArea, values: Any, *, word: bool) -> None:
    if values in (None, {}):
        return
    if isinstance(values, list):
        items = enumerate(values)
    elif isinstance(values, Mapping):
        items = values.items()
    else:
        raise ValueError(f"{area.name}.values must be a list or mapping")

    for raw_address, value in items:
        try:
            address = int(raw_address)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{area.name}: invalid initial address {raw_address!r}") from exc
        if word:
            assert isinstance(area, WordArea)
            area.write_words(address, [value])
        else:
            assert isinstance(area, BitArea)
            area.write_bits(address, [value])
