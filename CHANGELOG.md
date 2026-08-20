# Changelog

## 0.4.0

- Added a dependency-free browser dashboard hosted from the same process as the UDP PLC endpoints.
- Added Overview, Memory, and Traffic views for endpoint health, counters, live diagnostics, and shared PLC memory editing.
- Added JSON APIs for health, runtime status, incremental logs, memory reads/writes, and live logging-mode changes.
- Added a bounded thread-safe dashboard log ring with endpoint traffic and fault-event metrics.
- Added prevalidated multi-cell word/bit memory edits and a `--no-web-write` read-only mode.
- Added `--web`, `--web-bind`, `--web-port`, `--web-write`, `--web-max-points`, `--web-log-buffer`, and `--open-browser` launch options.
- Added packaged static assets with no Node.js build or additional runtime dependency.
- Exposed the dashboard on `8080/tcp` in Docker and Docker Compose.

## 0.3.0

- Added switchable `quiet`, `normal`, `debug`, and `trace` logging presets.
- Added independent traffic summary/HEX and memory write/read logging channels.
- Added parsed MC, FINS, and Modbus request/response diagnostics with request IDs and elapsed time.
- Added text and JSON Lines output, rotating file logs, bounded HEX dumps, and bounded value previews.
- Added fault-injection, no-response, startup, shutdown, and endpoint lifecycle records.
- Added a repository-root `main.py` launcher; `python main.py` starts `config/example.yml` without an editable install.
- Added command-line logging overrides such as `python main.py --trace`.

## 0.2.0

- Added an auto-detecting Mitsubishi MC endpoint for A-compatible 1E and QnA-compatible 3E/4E UDP frames.
- Added binary and ASCII encoding support on the same UDP port.
- Added batch, random, monitor, multiple-block, type-name, remote-control, loopback, and error-clear command handling.
- Added standard and extended device specifications for 3E/4E subcommands `0000` through `0003`.
- Added model-profile controls for frame types, encodings, and enabled/disabled commands.
- Added validated configurable device mappings, including A-compatible 1E device codes.
- Added per-client monitor state, fail-closed validation, atomic multi-write behavior, and deterministic protocol errors.
- Expanded unit and live UDP integration tests.
