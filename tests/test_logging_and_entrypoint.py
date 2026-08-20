from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

from plcmock.cli import _apply_logging_overrides, build_parser
from plcmock.config import parse_config
from plcmock.diagnostics import describe_request, describe_response


ROOT = Path(__file__).parents[1]


def minimal_config(server: dict | None = None):
    return parse_config(
        {
            "server": server or {},
            "memory": {"words": {"D": 32}, "bits": {"M": 512}},
            "endpoints": [
                {
                    "name": "mc",
                    "protocol": "mc-protocol",
                    "bind": "127.0.0.1",
                    "port": 0,
                }
            ],
        },
        source=ROOT / "test.yml",
    )


def test_logging_mode_defaults_and_legacy_hex_dump() -> None:
    normal = minimal_config()
    assert normal.server.log_mode == "normal"
    assert normal.server.log_level == "INFO"
    assert normal.server.traffic_log == "summary"
    assert normal.server.memory_log == "off"

    trace = minimal_config({"logging": {"mode": "trace", "format": "json"}})
    assert trace.server.log_level == "TRACE"
    assert trace.server.traffic_log == "hex"
    assert trace.server.memory_log == "all"
    assert trace.server.log_format == "json"
    assert trace.server.hex_dump is True

    legacy = minimal_config({"log_level": "DEBUG", "hex_dump": True})
    assert legacy.server.log_level == "DEBUG"
    assert legacy.server.traffic_log == "hex"
    assert legacy.server.hex_dump is True


def test_cli_trace_preset_can_be_selectively_overridden() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "serve",
            "--config",
            "config/example.yml",
            "--trace",
            "--traffic-log",
            "summary",
            "--memory-log",
            "write",
            "--max-hex-bytes",
            "64",
        ]
    )
    updated = _apply_logging_overrides(minimal_config(), args)
    assert updated.server.log_mode == "trace"
    assert updated.server.log_level == "TRACE"
    assert updated.server.traffic_log == "summary"
    assert updated.server.memory_log == "write"
    assert updated.server.max_hex_bytes == 64


def test_mc_diagnostics_include_command_device_address_and_end_code() -> None:
    spec = SimpleNamespace(name="D", radix=10)
    qna = SimpleNamespace(devices={0xA8: spec}, ascii_devices={"D": spec})
    plugin = SimpleNamespace(protocol_name="mc-protocol", qna=qna, slmp=qna)

    route = bytes.fromhex("00 ff ff 03 00")
    target = (100).to_bytes(3, "little") + bytes([0xA8]) + (2).to_bytes(2, "little")
    body = bytes.fromhex("10 00 01 04 00 00") + target
    request = bytes.fromhex("50 00") + route + len(body).to_bytes(2, "little") + body
    response_body = bytes.fromhex("00 00 34 12 cd ab")
    response = (
        bytes.fromhex("d0 00")
        + route
        + len(response_body).to_bytes(2, "little")
        + response_body
    )

    request_description = describe_request(plugin, request)
    assert "batch-read" in request_description.summary
    assert request_description.fields["device"] == "D"
    assert request_description.fields["address"] == 100
    assert request_description.fields["points"] == 2

    response_description = describe_response(plugin, request, response)
    assert response_description.fields["end_code"] == 0
    assert response_description.fields["response_data_bytes"] == 4

    one_e_plugin = SimpleNamespace(protocol_name="mc-1e")
    one_e_request = b"03FF0010" + b"44200000006401001234"
    one_e_response = b"8300"
    one_e_description = describe_response(
        one_e_plugin, one_e_request, one_e_response
    )
    assert one_e_description.fields["end_code"] == 0
    assert one_e_description.fields["response_data_bytes"] == 0


def test_source_tree_main_defaults_to_serve_and_example_config() -> None:
    spec = importlib.util.spec_from_file_location("plcmock_root_main", ROOT / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.normalize_argv([]) == [
        "serve",
        "--config",
        str(ROOT / "config" / "example.yml"),
    ]
    assert module.normalize_argv(["--trace"])[0:3] == [
        "serve",
        "--config",
        str(ROOT / "config" / "example.yml"),
    ]
    assert module.normalize_argv(["check", "--json"])[0:3] == [
        "check",
        "--config",
        str(ROOT / "config" / "example.yml"),
    ]
    assert module.normalize_argv(["--config", "custom.yml", "--debug"]) == [
        "serve",
        "--config",
        "custom.yml",
        "--debug",
    ]


def test_source_tree_main_check_runs_without_editable_install() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "check", "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["logging"]["traffic"] == "summary"
    assert any(item["protocol"] == "mc-protocol" for item in payload["endpoints"])
