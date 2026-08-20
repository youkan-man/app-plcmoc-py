from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import sys
from typing import Any, Mapping, Type

from plcmock.memory import MemorySpace

from .base import ProtocolPlugin
from .fins_udp import FinsUdpProtocol
from .modbus_udp import ModbusUdpProtocol
from .slmp import SlmpProtocol


BUILTIN_PROTOCOLS: dict[str, type[ProtocolPlugin]] = {
    "slmp": SlmpProtocol,
    "mc": SlmpProtocol,
    "mc-protocol": SlmpProtocol,
    "fins": FinsUdpProtocol,
    "fins-udp": FinsUdpProtocol,
    "modbus-udp": ModbusUdpProtocol,
}


def load_protocol(
    specification: str,
    *,
    memory: MemorySpace,
    options: Mapping[str, Any] | None = None,
    plugin_paths: tuple[Path, ...] = (),
) -> ProtocolPlugin:
    key = specification.strip().lower()
    protocol_class: Type[ProtocolPlugin]
    if key in BUILTIN_PROTOCOLS:
        protocol_class = BUILTIN_PROTOCOLS[key]
    else:
        _install_plugin_paths(plugin_paths)
        module_name, separator, class_name = specification.partition(":")
        if not separator:
            module_name, separator, class_name = specification.rpartition(".")
        if not module_name or not class_name:
            raise ValueError(
                f"unknown protocol {specification!r}; use a built-in name or module:Class"
            )
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise ValueError(f"cannot import protocol module {module_name!r}: {exc}") from exc
        try:
            candidate = getattr(module, class_name)
        except AttributeError as exc:
            raise ValueError(
                f"protocol class {class_name!r} does not exist in module {module_name!r}"
            ) from exc
        if not inspect.isclass(candidate) or not issubclass(candidate, ProtocolPlugin):
            raise ValueError(f"{specification!r} must resolve to a ProtocolPlugin subclass")
        protocol_class = candidate

    try:
        return protocol_class(memory, options)
    except Exception as exc:
        raise ValueError(f"cannot initialize protocol {specification!r}: {exc}") from exc


def _install_plugin_paths(paths: tuple[Path, ...]) -> None:
    for path in reversed(paths):
        if not path.is_dir():
            raise ValueError(f"plugin path is not a directory: {path}")
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
