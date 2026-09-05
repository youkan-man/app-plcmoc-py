from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import socket
from typing import Any, Iterator
import urllib.error
import urllib.request

import yaml

from plcmock.config import parse_config
from plcmock.server import UdpMockServer
from plcmock.web_dashboard import WebDashboardServer


ROOT = Path(__file__).parents[1]


def build_config():
    return parse_config(
        {
            "memory": {
                "words": {"D": {"size": 64, "values": {0: 100}}},
                "bits": {"M": {"size": 1024, "values": {0: True}}},
            },
            "endpoints": [
                {
                    "name": "mc",
                    "protocol": "mc-protocol",
                    "bind": "127.0.0.1",
                    "port": 0,
                }
            ],
        },
        source=ROOT / "web-test.yml",
    )


def request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        method=method,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        content_type = response.headers.get_content_type()
        decoded = json.loads(raw) if content_type == "application/json" else raw
        return response.status, decoded, content_type


@contextmanager
def dashboard(
    *, allow_write: bool = True
) -> Iterator[tuple[WebDashboardServer, str]]:
    config = build_config()
    plc_server = UdpMockServer(config)
    web = WebDashboardServer(
        config,
        plc_server,
        bind="127.0.0.1",
        port=0,
        allow_write=allow_write,
        max_memory_points=32,
        log_buffer_size=100,
    )

    async def start() -> None:
        await web.start()

    asyncio.run(start())
    assert web.url is not None
    try:
        yield web, web.url
    finally:
        asyncio.run(web.close())


def test_dashboard_hosts_assets_status_settings_and_memory_editor() -> None:
    with dashboard() as (web, base):
        status_code, body, content_type = request(base, "/")
        assert status_code == 200
        assert content_type == "text/html"
        assert b"PLC Mock Control" in body
        assert b"Settings" in body

        status_code, status, _ = request(base, "/api/status")
        assert status_code == 200
        assert status["ok"] is True
        assert status["web"]["allow_write"] is True
        assert status["memory"]["words"] == [{"name": "D", "size": 64}]
        assert status["memory"]["estimated_bytes"] == 64 * 2 + 1024
        assert status["endpoints"][0]["name"] == "mc"
        assert "rates" in status and "history" in status
        assert "system" in status

        status_code, settings, _ = request(base, "/api/settings")
        assert status_code == 200
        assert settings["writable"] is True
        assert settings["runtime_only"] is True
        assert settings["endpoints"][0]["config"]["protocol"] == "mc-protocol"
        schema_paths = {
            item["path"] for item in settings["endpoints"][0]["option_schema"]
        }
        assert {"accepted_frames", "model_name", "max_word_points"} <= schema_paths

        _, before, _ = request(
            base,
            "/api/memory?storage=word&area=D&start=0&count=3",
        )
        assert before["values"] == [100, 0, 0]

        status_code, written, _ = request(
            base,
            "/api/memory",
            method="PUT",
            payload={
                "storage": "word",
                "area": "D",
                "items": [
                    {"address": 1, "value": "0x1234"},
                    {"address": 2, "value": 99},
                ],
            },
        )
        assert status_code == 200
        assert written["items"] == [
            {"address": 1, "value": 0x1234},
            {"address": 2, "value": 99},
        ]
        assert web.plc_server.memory.word("D").read_words(0, 3) == [
            100,
            0x1234,
            99,
        ]

        status_code, bit_written, _ = request(
            base,
            "/api/memory",
            method="PUT",
            payload={
                "storage": "bit",
                "area": "M",
                "start": 1,
                "values": [1, 0, 1],
            },
        )
        assert status_code == 200
        assert bit_written["values"] == [1, 0, 1]
        assert web.plc_server.memory.bit("M").read_bits(0, 4) == [
            True,
            True,
            False,
            True,
        ]


def test_dashboard_read_only_and_atomic_validation() -> None:
    with dashboard(allow_write=False) as (_, base):
        status_code, body, _ = request(
            base,
            "/api/memory",
            method="PUT",
            payload={
                "storage": "word",
                "area": "D",
                "start": 0,
                "values": [1],
            },
        )
        assert status_code == 403
        assert "disabled" in body["error"]

        status_code, _, _ = request(
            base,
            "/api/logging",
            method="POST",
            payload={"mode": "trace"},
        )
        assert status_code == 403

        status_code, _, _ = request(
            base,
            "/api/endpoints/mc",
            method="PUT",
            payload={
                "running": False,
                "bind": "127.0.0.1",
                "port": 0,
                "protocol": "mc-protocol",
                "options": {},
                "faults": {},
            },
        )
        assert status_code == 403

    with dashboard() as (web, base):
        status_code, _, _ = request(
            base,
            "/api/memory",
            method="PUT",
            payload={
                "storage": "word",
                "area": "D",
                "items": [
                    {"address": 1, "value": 123},
                    {"address": 64, "value": 456},
                ],
            },
        )
        assert status_code == 400
        assert web.plc_server.memory.word("D").read_words(1, 1) == [0]


def test_dashboard_log_buffer_filters_and_runtime_mode() -> None:
    with dashboard() as (web, base):
        logger = logging.getLogger("plcmock.traffic")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            logger.info(
                "MC 3E binary batch-read device=D address=100 points=2",
                extra={
                    "event": "datagram_received",
                    "endpoint": "mc",
                    "protocol": "mc-protocol",
                    "request_id": "mc-00000001",
                    "payload_bytes": 21,
                },
            )
        finally:
            logger.setLevel(previous_level)

        _, logs, _ = request(
            base,
            "/api/logs?after=0&limit=20&endpoint=mc&search=device%3DD",
        )
        assert len(logs["records"]) == 1
        assert logs["records"][0]["request_id"] == "mc-00000001"

        # Direct telemetry is deliberately independent from emitted logs.
        _, status, _ = request(base, "/api/status")
        assert status["metrics"]["received"] == 0
        assert status["endpoints"][0]["metrics"]["received"] == 0

        status_code, changed, _ = request(
            base,
            "/api/logging",
            method="POST",
            payload={
                "mode": "trace",
                "traffic": "summary",
                "memory": "write",
                "max_hex_bytes": 64,
            },
        )
        assert status_code == 200
        assert changed["logging"] == {
            "mode": "trace",
            "level": "TRACE",
            "traffic": "summary",
            "memory": "write",
        }
        assert logging.getLogger("plcmock.memory").level == logging.DEBUG
        assert web.plc_server.server_config.max_hex_bytes == 64

        request(
            base,
            "/api/logging",
            method="POST",
            payload={"mode": "normal"},
        )
        status_code, _, _ = request(
            base,
            "/api/logs/clear",
            method="POST",
            payload={},
        )
        assert status_code == 200
        _, empty, _ = request(base, "/api/logs?after=0&limit=20")
        assert empty["records"] == []


def _mc_read_d0() -> bytes:
    route = bytes.fromhex("00 ff ff 03 00")
    payload = (
        (0).to_bytes(3, "little")
        + bytes([0xA8])
        + (1).to_bytes(2, "little")
    )
    body = bytes.fromhex("10 00 01 04 00 00") + payload
    return (
        bytes.fromhex("50 00")
        + route
        + len(body).to_bytes(2, "little")
        + body
    )


def _udp_exchange(target: tuple[str, int], payload: bytes) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(payload, target)
        return sock.recvfrom(65535)[0]


def test_live_dashboard_can_reconfigure_restart_and_observe_endpoint() -> None:
    async def scenario() -> None:
        config = build_config()
        server = UdpMockServer(config)
        web = WebDashboardServer(
            config,
            server,
            bind="127.0.0.1",
            port=0,
            max_memory_points=32,
            log_buffer_size=100,
        )
        await web.start()
        await server.start()
        assert web.url is not None
        try:
            # Quiet suppresses traffic logs, but must not stop telemetry.
            await asyncio.to_thread(
                request,
                web.url,
                "/api/logging",
                method="POST",
                payload={"mode": "quiet"},
            )
            target = server.bound_endpoints["mc"]
            response = await asyncio.to_thread(
                _udp_exchange,
                target,
                _mc_read_d0(),
            )
            assert response[-4:] == bytes.fromhex("00 00 64 00")

            _, status, _ = await asyncio.to_thread(
                request,
                web.url,
                "/api/status",
            )
            endpoint = status["endpoints"][0]
            assert endpoint["metrics"]["received"] == 1
            assert endpoint["metrics"]["sent"] == 1
            assert endpoint["client_count"] == 1
            assert endpoint["last_request_summary"].startswith("MC 3E binary")

            _, settings, _ = await asyncio.to_thread(
                request,
                web.url,
                "/api/settings",
            )
            initial_generation = settings["endpoints"][0]["generation"]
            payload = {
                "running": True,
                "bind": "127.0.0.1",
                "port": 0,
                "protocol": "mc-protocol",
                "options": {
                    "accepted_frames": ["3E"],
                    "accepted_encodings": ["binary"],
                    "model_name": "UI MOCK",
                },
                "faults": {
                    "seed": 7,
                    "drop_rate": 0.0,
                    "duplicate_rate": 0.0,
                    "corrupt_rate": 0.0,
                    "delay_ms": {"min": 1, "max": 2},
                },
            }
            status_code, applied, _ = await asyncio.to_thread(
                request,
                web.url,
                "/api/endpoints/mc",
                method="PUT",
                payload=payload,
            )
            assert status_code == 200
            endpoint = applied["endpoint"]
            assert endpoint["running"] is True
            assert endpoint["generation"] > initial_generation
            assert endpoint["faults"]["delay_ms"] == {
                "min": 1.0,
                "max": 2.0,
            }
            assert endpoint["protocol_state"]["qna"]["model_name"] == "UI MOCK"

            _, stopped, _ = await asyncio.to_thread(
                request,
                web.url,
                "/api/endpoints/mc/action",
                method="POST",
                payload={"action": "stop"},
            )
            assert stopped["endpoint"]["running"] is False
            assert stopped["endpoint"]["desired_running"] is False

            _, started, _ = await asyncio.to_thread(
                request,
                web.url,
                "/api/endpoints/mc/action",
                method="POST",
                payload={"action": "start"},
            )
            assert started["endpoint"]["running"] is True

            status_code, exported, content_type = await asyncio.to_thread(
                request,
                web.url,
                "/api/config/export",
            )
            assert status_code == 200
            assert content_type == "application/yaml"
            exported_config = yaml.safe_load(exported)
            assert exported_config["endpoints"][0]["options"]["model_name"] == "UI MOCK"
            assert exported_config["endpoints"][0]["faults"]["seed"] == 7

            status_code, _, _ = await asyncio.to_thread(
                request,
                web.url,
                "/api/metrics/reset",
                method="POST",
                payload={},
            )
            assert status_code == 200
            assert server.runtime_snapshot()["metrics"]["received"] == 0
        finally:
            await web.close()
            await server.close()
            logging.getLogger("plcmock").setLevel(logging.INFO)
            logging.getLogger("plcmock.traffic").setLevel(logging.INFO)
            logging.getLogger("plcmock.memory").setLevel(logging.CRITICAL + 1)

    asyncio.run(scenario())


def test_web_cli_defaults_can_be_disabled_or_rebound() -> None:
    from plcmock.cli import build_parser

    parser = build_parser()
    defaults = parser.parse_args(["serve", "--config", "config/example.yml"])
    assert defaults.web is True
    assert defaults.web_bind == "0.0.0.0"
    assert defaults.web_port == 8080
    assert defaults.web_write is True

    disabled = parser.parse_args(
        [
            "serve",
            "--config",
            "config/example.yml",
            "--no-web",
            "--web-bind",
            "127.0.0.1",
            "--web-port",
            "18080",
            "--no-web-write",
        ]
    )
    assert disabled.web is False
    assert disabled.web_bind == "127.0.0.1"
    assert disabled.web_port == 18080
    assert disabled.web_write is False
