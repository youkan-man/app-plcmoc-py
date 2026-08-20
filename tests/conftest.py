from __future__ import annotations

from plcmock.memory import MemorySpace


WORD_AREAS = (
    "D",
    "W",
    "R",
    "ZR",
    "SD",
    "SW",
    "Z",
    "TN",
    "CN",
    "SN",
    "CIO",
    "WR",
    "HR",
    "AR",
    "EM0",
    "INPUT",
)

BIT_AREAS = (
    "M",
    "X",
    "Y",
    "L",
    "F",
    "V",
    "S",
    "B",
    "SM",
    "SB",
    "DX",
    "DY",
    "TC",
    "TS",
    "CC",
    "CS",
    "SC",
    "SS",
)


def build_memory() -> MemorySpace:
    return MemorySpace.from_config(
        {
            "words": {name: {"size": 4096} for name in WORD_AREAS},
            "bits": {name: {"size": 65536} for name in BIT_AREAS},
        }
    )
