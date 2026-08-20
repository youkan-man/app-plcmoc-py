"""Extensible UDP PLC protocol mock server."""

from .memory import AddressOutOfRange, MemorySpace, UnknownArea

__all__ = ["AddressOutOfRange", "MemorySpace", "UnknownArea"]
__version__ = "0.1.0"
