from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Mapping

import yaml


LOG_MODE_DEFAULTS: dict[str, tuple[str, str, str]] = {
    # mode: (base log level, traffic logging, memory logging)
    "quiet": ("WARNING", "off", "off"),
    "normal": ("INFO", "summary", "off"),
    "debug": ("DEBUG", "summary", "write"),
    "trace": ("TRACE", "hex", "all"),
}
LOG_FORMATS = {"text", "json"}
TRAFFIC_LOG_MODES = {"off", "summary", "hex"}
MEMORY_LOG_MODES = {"off", "write", "all"}
LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass(frozen=True, slots=True)
class ServerConfig:
    # The first three fields keep the 0.1/0.2 constructor shape compatible.
    log_level: str = "INFO"
    hex_dump: bool = False
    max_datagram_size: int = 65535

    log_mode: str = "normal"
    log_format: str = "text"
    log_console: bool = True
    log_file: Path | None = None
    log_rotate_max_bytes: int = 10 * 1024 * 1024
    log_rotate_backup_count: int = 5
    traffic_log: str = "summary"
    memory_log: str = "off"
    max_hex_bytes: int = 512
    max_value_preview: int = 16


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


def logging_mode_defaults(mode: str) -> tuple[str, str, str]:
    try:
        return LOG_MODE_DEFAULTS[mode.lower()]
    except KeyError as exc:
        allowed = ", ".join(sorted(LOG_MODE_DEFAULTS))
        raise ValueError(f"log mode must be one of: {allowed}") from exc


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
    base_dir = source.parent
    server_raw = _mapping(raw.get("server", {}), "server")
    logging_raw = _mapping(server_raw.get("logging", {}), "server.logging")

    mode = _choice(
        logging_raw.get("mode", server_raw.get("log_mode", "normal")),
        "server.logging.mode",
        set(LOG_MODE_DEFAULTS),
    )
    preset_level, preset_traffic, preset_memory = logging_mode_defaults(mode)

    level_source = logging_raw.get(
        "level", server_raw.get("log_level", preset_level)
    )
    level = _log_level(level_source, "server.logging.level")

    traffic_source: Any
    if "traffic" in logging_raw:
        traffic_source = logging_raw["traffic"]
    elif "traffic_log" in server_raw:
        traffic_source = server_raw["traffic_log"]
    elif "hex_dump" in server_raw and _boolean(
        server_raw["hex_dump"], "server.hex_dump"
    ):
        traffic_source = "hex"
    else:
        traffic_source = preset_traffic
    traffic_log = _switch_choice(
        traffic_source,
        "server.logging.traffic",
        TRAFFIC_LOG_MODES,
        true_value="summary",
    )

    memory_log = _switch_choice(
        logging_raw.get("memory", server_raw.get("memory_log", preset_memory)),
        "server.logging.memory",
        MEMORY_LOG_MODES,
        true_value="all",
    )
    log_format = _choice(
        logging_raw.get("format", server_raw.get("log_format", "text")),
        "server.logging.format",
        LOG_FORMATS,
    )
    log_console = _boolean(
        logging_raw.get("console", server_raw.get("log_console", True)),
        "server.logging.console",
    )
    log_file = _optional_path(
        logging_raw.get("file", server_raw.get("log_file")),
        "server.logging.file",
        base_dir=base_dir,
    )
    log_rotate_max_bytes = _integer(
        logging_raw.get(
            "rotate_max_bytes",
            server_raw.get("log_rotate_max_bytes", 10 * 1024 * 1024),
        ),
        "server.logging.rotate_max_bytes",
        minimum=0,
        maximum=2**31 - 1,
    )
    log_rotate_backup_count = _integer(
        logging_raw.get(
            "rotate_backup_count",
            server_raw.get("log_rotate_backup_count", 5),
        ),
        "server.logging.rotate_backup_count",
        minimum=0,
        maximum=100,
    )
    max_hex_bytes = _integer(
        logging_raw.get(
            "max_hex_bytes", server_raw.get("max_hex_bytes", 512)
        ),
        "server.logging.max_hex_bytes",
        minimum=0,
        maximum=65535,
    )
    max_value_preview = _integer(
        logging_raw.get(
            "max_value_preview", server_raw.get("max_value_preview", 16)
        ),
        "server.logging.max_value_preview",
        minimum=1,
        maximum=4096,
    )
    max_datagram_size = _integer(
        server_raw.get("max_datagram_size", 65535),
        "server.max_datagram_size",
        minimum=512,
        maximum=65535,
    )
    server = ServerConfig(
        log_level=level,
        hex_dump=traffic_log == "hex",
        max_datagram_size=max_datagram_size,
        log_mode=mode,
        log_format=log_format,
        log_console=log_console,
        log_file=log_file,
        log_rotate_max_bytes=log_rotate_max_bytes,
        log_rotate_backup_count=log_rotate_backup_count,
        traffic_log=traffic_log,
        memory_log=memory_log,
        max_hex_bytes=max_hex_bytes,
        max_value_preview=max_value_preview,
    )

    paths_raw = raw.get("plugin_paths", [])
    if not isinstance(paths_raw, list):
        raise ValueError("plugin_paths must be a list")
    plugin_paths: list[Path] = []
    for index, item in enumerate(paths_raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"plugin_paths[{index}] must be a non-empty string")
        path = Path(item).expanduser()
        plugin_paths.append(
            (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
        )

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
        bind = _nonempty_string(
            endpoint.get("bind", "0.0.0.0"), f"{where}.bind"
        )
        port = _integer(
            endpoint.get("port"), f"{where}.port", minimum=0, maximum=65535
        )
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
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{where} must be an integer in {minimum}..{maximum}")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be true or false")
    return value


def _choice(value: Any, where: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value.lower() not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{where} must be one of: {choices}")
    return value.lower()


def _switch_choice(
    value: Any,
    where: str,
    allowed: set[str],
    *,
    true_value: str,
) -> str:
    # PyYAML follows YAML 1.1 and parses an unquoted ``off`` as False. Treat
    # booleans as convenient switches so ``memory: off`` behaves as written.
    if isinstance(value, bool):
        value = true_value if value else "off"
    return _choice(value, where, allowed)


def _log_level(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a logging level name")
    normalized = value.strip().upper()
    if normalized == "WARN":
        normalized = "WARNING"
    if normalized not in LOG_LEVELS:
        choices = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(f"{where} must be one of: {choices}")
    # Keep logging imported here so an invalid future stdlib level cannot slip
    # through merely because it was added to the local allow-list.
    if normalized != "TRACE" and logging.getLevelName(normalized) == f"Level {normalized}":
        raise ValueError(f"{where} is not supported by this Python runtime")
    return normalized


def _optional_path(value: Any, where: str, *, base_dir: Path) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty path string or null")
    path = Path(value).expanduser()
    return (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
