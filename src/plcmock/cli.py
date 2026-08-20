from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import json
import logging
from pathlib import Path
import signal
import sys

from .config import (
    AppConfig,
    LOG_FORMATS,
    LOG_LEVELS,
    LOG_MODE_DEFAULTS,
    MEMORY_LOG_MODES,
    TRAFFIC_LOG_MODES,
    load_config,
    logging_mode_defaults,
)
from .logging_config import configure_logging
from .memory import MemorySpace
from .protocols.loader import load_protocol
from .server import UdpMockServer


LOGGER = logging.getLogger("plcmock.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plcmock",
        description="Extensible UDP PLC mock server",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser(
        "serve",
        help="start all configured UDP endpoints",
    )
    serve.add_argument("--config", "-c", required=True, type=Path)
    _add_logging_arguments(serve)

    check = subparsers.add_parser(
        "check",
        help="validate configuration and protocol plugins",
    )
    check.add_argument("--config", "-c", required=True, type=Path)
    check.add_argument(
        "--json",
        action="store_true",
        help="print validation summary as JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _check(args.config, as_json=args.json)
        if args.command == "serve":
            return asyncio.run(_serve(args.config, args))
    except (ValueError, OSError) as exc:
        print(f"plcmock: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 1


def _add_logging_arguments(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--log-mode",
        choices=sorted(LOG_MODE_DEFAULTS),
        help="logging preset: quiet, normal, debug, or trace",
    )
    mode.add_argument(
        "--quiet",
        dest="log_mode",
        action="store_const",
        const="quiet",
        help="shortcut for --log-mode quiet",
    )
    mode.add_argument(
        "--debug",
        dest="log_mode",
        action="store_const",
        const="debug",
        help="shortcut for --log-mode debug",
    )
    mode.add_argument(
        "--trace",
        dest="log_mode",
        action="store_const",
        const="trace",
        help="shortcut for --log-mode trace",
    )
    parser.add_argument(
        "--log-level",
        choices=sorted(LOG_LEVELS),
        help="override the application log level",
    )
    parser.add_argument(
        "--log-format",
        choices=sorted(LOG_FORMATS),
        help="text or JSON Lines output",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="also write rotating logs to this file",
    )
    parser.add_argument(
        "--console-log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable stderr logging",
    )
    traffic = parser.add_mutually_exclusive_group()
    traffic.add_argument(
        "--traffic-log",
        choices=sorted(TRAFFIC_LOG_MODES),
        help="datagram logging: off, summary, or hex",
    )
    traffic.add_argument(
        "--hex-dump",
        dest="traffic_log",
        action="store_const",
        const="hex",
        help="legacy shortcut for --traffic-log hex",
    )
    traffic.add_argument(
        "--no-traffic-log",
        dest="traffic_log",
        action="store_const",
        const="off",
        help="disable request/response traffic logs",
    )
    parser.add_argument(
        "--memory-log",
        choices=sorted(MEMORY_LOG_MODES),
        help="memory logging: off, write, or all",
    )
    parser.add_argument(
        "--max-hex-bytes",
        type=int,
        help="maximum bytes shown in each HEX dump",
    )


def _check(path: Path, *, as_json: bool) -> int:
    config = load_config(path)
    memory = MemorySpace.from_config(config.memory)
    protocols = []
    for endpoint in config.endpoints:
        plugin = load_protocol(
            endpoint.protocol,
            memory=memory,
            options=endpoint.options,
            plugin_paths=config.plugin_paths,
        )
        protocols.append(
            {
                "endpoint": endpoint.name,
                "protocol": plugin.protocol_name,
                "bind": endpoint.bind,
                "port": endpoint.port,
            }
        )
    summary = {
        "ok": True,
        "source": str(config.source),
        "logging": {
            "mode": config.server.log_mode,
            "level": config.server.log_level,
            "format": config.server.log_format,
            "console": config.server.log_console,
            "file": str(config.server.log_file) if config.server.log_file else None,
            "traffic": config.server.traffic_log,
            "memory": config.server.memory_log,
            "max_hex_bytes": config.server.max_hex_bytes,
        },
        "memory": memory.describe(),
        "endpoints": protocols,
    }
    if as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"OK: {config.source}")
        print(
            "  logging: "
            f"mode={config.server.log_mode} level={config.server.log_level} "
            f"format={config.server.log_format} traffic={config.server.traffic_log} "
            f"memory={config.server.memory_log}"
        )
        for endpoint in protocols:
            print(
                f"  {endpoint['endpoint']}: {endpoint['protocol']} "
                f"udp://{endpoint['bind']}:{endpoint['port']}"
            )
    return 0


async def _serve(path: Path, args: argparse.Namespace) -> int:
    config = _apply_logging_overrides(load_config(path), args)
    configure_logging(config.server)
    LOGGER.info(
        "launching server config=%s log_mode=%s level=%s traffic=%s memory=%s",
        config.source,
        config.server.log_mode,
        config.server.log_level,
        config.server.traffic_log,
        config.server.memory_log,
        extra={
            "event": "launcher_start",
            "config": str(config.source),
            "log_mode": config.server.log_mode,
            "log_level": config.server.log_level,
            "traffic_log": config.server.traffic_log,
            "memory_log": config.server.memory_log,
        },
    )
    server = UdpMockServer(config)
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        LOGGER.info("shutdown signal received", extra={"event": "shutdown_signal"})
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            loop.add_signal_handler(signum, request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await stop.wait()
    finally:
        await server.close()
    return 0


def _apply_logging_overrides(
    config: AppConfig,
    args: argparse.Namespace,
) -> AppConfig:
    server = config.server
    log_mode = getattr(args, "log_mode", None)
    if log_mode is not None:
        level, traffic, memory = logging_mode_defaults(log_mode)
        server = replace(
            server,
            log_mode=log_mode,
            log_level=level,
            traffic_log=traffic,
            memory_log=memory,
            hex_dump=traffic == "hex",
        )

    log_level = getattr(args, "log_level", None)
    if log_level is not None:
        server = replace(server, log_level=log_level)
    log_format = getattr(args, "log_format", None)
    if log_format is not None:
        server = replace(server, log_format=log_format)
    log_file = getattr(args, "log_file", None)
    if log_file is not None:
        server = replace(server, log_file=log_file.expanduser().resolve())
    log_console = getattr(args, "console_log", None)
    if log_console is not None:
        server = replace(server, log_console=log_console)
    traffic_log = getattr(args, "traffic_log", None)
    if traffic_log is not None:
        server = replace(
            server,
            traffic_log=traffic_log,
            hex_dump=traffic_log == "hex",
        )
    memory_log = getattr(args, "memory_log", None)
    if memory_log is not None:
        server = replace(server, memory_log=memory_log)
    max_hex_bytes = getattr(args, "max_hex_bytes", None)
    if max_hex_bytes is not None:
        if not 0 <= max_hex_bytes <= 65535:
            raise ValueError("--max-hex-bytes must be in 0..65535")
        server = replace(server, max_hex_bytes=max_hex_bytes)
    return replace(config, server=server)
