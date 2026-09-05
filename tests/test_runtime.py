from __future__ import annotations

from types import SimpleNamespace

from plcmock.runtime import (
    EndpointTelemetry,
    aggregate_endpoint_snapshots,
    protocol_snapshot,
)


def test_endpoint_telemetry_tracks_traffic_clients_latency_and_reset() -> None:
    telemetry = EndpointTelemetry("mc", history_seconds=60, client_limit=2)
    telemetry.mark_started(("127.0.0.1", 5000))
    telemetry.received(21, "10.0.0.1:40000", "mc-00000001")
    telemetry.describe_request("MC 3E batch-read D100")
    telemetry.request_started()
    telemetry.sent(15, response_summary="MC response end=0x0000")
    telemetry.request_finished(1.25, "MC response end=0x0000")
    telemetry.received(12, "10.0.0.2:40001", "mc-00000002")
    telemetry.no_response(fault_drop=True)
    telemetry.fault_corruption()
    telemetry.fault_duplicate()

    snapshot = telemetry.snapshot()
    assert snapshot["running"] is True
    assert snapshot["metrics"] == {
        "received": 2,
        "sent": 1,
        "bytes_received": 33,
        "bytes_sent": 15,
        "no_response": 1,
        "errors": 0,
        "fault_drops": 1,
        "fault_corruptions": 1,
        "fault_duplicates": 1,
        "rejected": 0,
    }
    assert snapshot["average_latency_ms"] == 1.25
    assert snapshot["client_count"] == 2
    assert snapshot["last_request_id"] == "mc-00000002"
    assert len(snapshot["history"]) == 60

    telemetry.reset_metrics()
    reset = telemetry.snapshot()
    assert reset["metrics"]["received"] == 0
    assert reset["client_count"] == 0
    assert reset["running"] is True


def test_aggregate_endpoint_snapshots_combines_metrics_and_history() -> None:
    first = EndpointTelemetry("a")
    second = EndpointTelemetry("b")
    first.received(10, "a:1", "a-1")
    first.sent(8)
    second.received(20, "b:1", "b-1")
    second.error("boom")

    aggregate = aggregate_endpoint_snapshots(
        [first.snapshot(history_seconds=2), second.snapshot(history_seconds=2)]
    )
    assert aggregate["metrics"]["received"] == 2
    assert aggregate["metrics"]["sent"] == 1
    assert aggregate["metrics"]["bytes_received"] == 30
    assert aggregate["metrics"]["errors"] == 1
    assert aggregate["history"][-1]["received"] == 2


def test_protocol_snapshot_exposes_known_runtime_state_without_objects() -> None:
    qna = SimpleNamespace(
        cpu_state="RUN",
        last_clear_mode=0,
        error_code=0,
        model_name="R08CPU",
        model_code=0x4801,
        accepted_frames={"3E", "4E"},
        accepted_encodings={"binary"},
        enabled_commands={0x0401, 0x1401},
        allow_remote_control=True,
    )
    one_e = SimpleNamespace(
        accepted_frames={"1E"},
        accepted_encodings={"binary"},
        enabled_commands={0x00, 0x01},
        pc_number=255,
        accept_any_pc=True,
        max_points=256,
    )
    plugin = SimpleNamespace(
        protocol_name="mc-protocol",
        qna=qna,
        one_e=one_e,
    )

    snapshot = protocol_snapshot(plugin)
    assert snapshot["family"] == "mitsubishi-mc"
    assert snapshot["qna"]["cpu_state"] == "RUN"
    assert snapshot["qna"]["accepted_frames"] == ["3E", "4E"]
    assert snapshot["one_e"]["enabled_commands"] == [0, 1]


def test_endpoint_reconfiguration_rolls_back_after_bind_failure() -> None:
    import asyncio
    from pathlib import Path
    import socket

    from plcmock.config import EndpointConfig, parse_config
    from plcmock.server import UdpMockServer

    async def scenario() -> None:
        config = parse_config(
            {
                "memory": {
                    "words": {"D": 64, "INPUT": 64},
                    "bits": {"M": 1024, "X": 1024},
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
            source=Path.cwd() / "runtime-test.yml",
        )
        server = UdpMockServer(config)
        await server.start()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", 0))
        occupied_port = blocker.getsockname()[1]
        try:
            replacement = EndpointConfig(
                name="mc",
                protocol="mc-protocol",
                bind="127.0.0.1",
                port=occupied_port,
                options={},
                faults={},
            )
            try:
                await server.apply_endpoint("mc", replacement, running=True)
            except RuntimeError as exc:
                assert "previous configuration restored" in str(exc)
            else:  # pragma: no cover - the OS must reject the occupied port
                raise AssertionError("occupied UDP port unexpectedly accepted")

            snapshot = server.endpoint_snapshot("mc")
            assert snapshot["running"] is True
            assert snapshot["configured_port"] == 0
            assert snapshot["last_error"] is not None
            assert "previous configuration restored" in snapshot["last_error"]
        finally:
            blocker.close()
            await server.close()

    asyncio.run(scenario())
