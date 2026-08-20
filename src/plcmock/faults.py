from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any, Mapping


@dataclass(slots=True)
class FaultPolicy:
    drop_rate: float = 0.0
    duplicate_rate: float = 0.0
    corrupt_rate: float = 0.0
    delay_min_ms: float = 0.0
    delay_max_ms: float = 0.0
    seed: int | None = None
    _random: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for name in ("drop_rate", "duplicate_rate", "corrupt_rate"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"faults.{name} must be in 0..1")
        if self.delay_min_ms < 0 or self.delay_max_ms < self.delay_min_ms:
            raise ValueError("faults.delay_ms must be non-negative and max >= min")
        self._random = random.Random(self.seed)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "FaultPolicy":
        raw = raw or {}
        delay = raw.get("delay_ms", 0)
        if isinstance(delay, Mapping):
            minimum = _number(delay.get("min", 0), "faults.delay_ms.min")
            maximum = _number(delay.get("max", minimum), "faults.delay_ms.max")
        else:
            minimum = maximum = _number(delay, "faults.delay_ms")
        seed = raw.get("seed")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise ValueError("faults.seed must be an integer or null")
        return cls(
            drop_rate=_number(raw.get("drop_rate", 0), "faults.drop_rate"),
            duplicate_rate=_number(raw.get("duplicate_rate", 0), "faults.duplicate_rate"),
            corrupt_rate=_number(raw.get("corrupt_rate", 0), "faults.corrupt_rate"),
            delay_min_ms=minimum,
            delay_max_ms=maximum,
            seed=seed,
        )

    def should_drop(self) -> bool:
        return self._random.random() < self.drop_rate

    def should_duplicate(self) -> bool:
        return self._random.random() < self.duplicate_rate

    def delay_seconds(self) -> float:
        if self.delay_max_ms <= 0:
            return 0.0
        return self._random.uniform(self.delay_min_ms, self.delay_max_ms) / 1000.0

    def maybe_corrupt(self, payload: bytes) -> bytes:
        if not payload or self._random.random() >= self.corrupt_rate:
            return payload
        mutable = bytearray(payload)
        index = self._random.randrange(len(mutable))
        mutable[index] ^= 1 << self._random.randrange(8)
        return bytes(mutable)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number")
    return float(value)
