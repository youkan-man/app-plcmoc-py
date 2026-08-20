from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class ServerConfig:
    log_level: str = "INFO"
    hex_dump: bool = False
    max_datagram_size: int = 65535


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    name: str
    protocol: str
    bind: str
    port: int
    options: dict[str, Any] = field(default_factory=dict)
    faults: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AppConfig:
    server: ServerConfig
    plugin_paths: tuple[Path, ...]
    memory: dict[str, Any]
    endpoints: tuple[EndpointConfig, ...]
    source: Path


def load_config(path: str | Path) -> AppConfig:
    source = Path(path).expanduser().resolve()
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"configuration file not found: {source}") from None
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {source}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("configuration root must be a mapping")
    return parse_config(raw, source=source)


def parse_config(raw: Mapping[str, Any], *, source: Path | None = None) -> AppConfig:
    source = (source or Path("<memory>")).resolve()
    server_raw = _mapping(raw.get("server", {}), "server")
    level = str(server_raw.get("log_level", "INFO")).upper()
    hex_dump = _boolean(server_raw.get("hex_dump", False), "server.hex_dump")
    max_datagram_size = _integer(
        server_raw.get("max_datagram_size", 65535),
        "server.max_datagram_size",
        minimum=512,
        maximum=65535,
    )
    server = ServerConfig(level, hex_dump, max_datagram_size)

    base_dir = source.parent
    paths_raw = raw.get("plugin_paths", [])
    if not isinstance(paths_raw, list):
        raise ValueError("plugin_paths must be a list")
    plugin_paths: list[Path] = []
    for index, item in enumerate(paths_raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"plugin_paths[{index}] must be a non-empty string")
        path = Path(item).expanduser()
        plugin_paths.append((base_dir / path).resolve() if not path.is_absolute() else path.resolve())

    memory = dict(_mapping(raw.get("memory", {}), "memory"))
    endpoints_raw = raw.get("endpoints")
    if not isinstance(endpoints_raw, list) or not endpoints_raw:
        raise ValueError("endpoints must be a non-empty list")

    endpoints: list[EndpointConfig] = []
    names: set[str] = set()
    bindings: set[tuple[str, int]] = set()
    for index, item in enumerate(endpoints_raw):
        where = f"endpoints[{index}]"
        endpoint = _mapping(item, where)
        name = _nonempty_string(endpoint.get("name"), f"{where}.name")
        protocol = _nonempty_string(endpoint.get("protocol"), f"{where}.protocol")
        bind = _nonempty_string(endpoint.get("bind", "0.0.0.0"), f"{where}.bind")
        port = _integer(endpoint.get("port"), f"{where}.port", minimum=0, maximum=65535)
        options = dict(_mapping(endpoint.get("options", {}), f"{where}.options"))
        faults = dict(_mapping(endpoint.get("faults", {}), f"{where}.faults"))
        if name in names:
            raise ValueError(f"duplicate endpoint name {name!r}")
        if port != 0 and (bind, port) in bindings:
            raise ValueError(f"duplicate UDP binding {bind}:{port}")
        names.add(name)
        bindings.add((bind, port))
        endpoints.append(EndpointConfig(name, protocol, bind, port, options, faults))

    return AppConfig(server, tuple(plugin_paths), memory, tuple(endpoints), source)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a mapping")
    return value


def _nonempty_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty string")
    return value.strip()


def _integer(value: Any, where: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{where} must be an integer in {minimum}..{maximum}")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be true or false")
    return value
