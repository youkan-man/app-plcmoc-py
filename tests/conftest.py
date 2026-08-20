from __future__ import annotations

from plcmock.memory import MemorySpace


def build_memory() -> MemorySpace:
    return MemorySpace.from_config(
        {
            "words": {
                "D": {"size": 2048},
                "W": {"size": 2048},
                "R": {"size": 2048},
                "ZR": {"size": 2048},
                "SD": {"size": 256},
                "CIO": {"size": 2048},
                "WR": {"size": 2048},
                "HR": {"size": 2048},
                "AR": {"size": 2048},
                "EM0": {"size": 2048},
                "INPUT": {"size": 2048},
            },
            "bits": {
                "M": {"size": 32768},
                "X": {"size": 32768},
                "Y": {"size": 32768},
                "L": {"size": 32768},
                "F": {"size": 32768},
                "V": {"size": 32768},
                "B": {"size": 32768},
                "SM": {"size": 4096},
            },
        }
    )
