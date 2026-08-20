from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import signal
import sys

from .config import load_config
from .memory import MemorySpace
from .protocols.loader import load_protocol
from .server import UdpMockServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plcmock", description="Extensible UDP PLC mock server")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start all configured UDP endpoints")
    serve.add_argument("--config", "-c", required=True, type=Path)

    check = subparsers.add_parser("check", help="validate configuration and protocol plugins")
    check.add_argument("--config", "-c", required=True, type=Path)
    check.add_argument("--json", action="store_true", help="print validation summary as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _check(args.config, as_json=args.json)
        if args.command == "serve":
            return asyncio.run(_serve(args.config))
    except (ValueError, OSError) as exc:
        print(f"plcmock: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 1


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
    summary = {"ok": True, "source": str(config.source), "memory": memory.describe(), "endpoints": protocols}
    if as_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"OK: {config.source}")
        for endpoint in protocols:
            print(
                f"  {endpoint['endpoint']}: {endpoint['protocol']} "
                f"udp://{endpoint['bind']}:{endpoint['port']}"
            )
    return 0


async def _serve(path: Path) -> int:
    config = load_config(path)
    logging.basicConfig(
        level=getattr(logging, config.server.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = UdpMockServer(config)
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
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
