# Runtime settings and status dashboard

Version 0.5.0 expands the browser dashboard from a viewer into a runtime control plane for the PLC mock.

## Views

### Overview

The Overview page reports telemetry directly from every UDP endpoint. Counters no longer depend on the selected logging mode, so requests and responses continue to be counted in `quiet` mode.

Displayed information includes:

- process health, uptime, Python version, PID, thread count, maximum RSS and load average;
- running and desired endpoint counts;
- cumulative RX/TX packets and bytes;
- five-second request/response rates;
- a 60-second traffic graph;
- active and peak concurrent requests;
- average and maximum handler latency;
- no-response, rejected-packet and runtime-error counters;
- injected drop, corruption and duplicate counters;
- last request, response, client, request ID and error;
- recent clients per endpoint;
- protocol-specific runtime state, including MC CPU state and enabled frame profile.

Selecting an endpoint opens its detail pane. Start, stop and restart actions are available there when web writes are enabled.

## Settings

The Settings page contains global logging controls and one configuration card per endpoint.

### Global logging

The following values can be changed without restarting the process:

- preset: `quiet`, `normal`, `debug`, `trace`;
- application log level;
- traffic log mode;
- memory log mode;
- maximum HEX bytes per packet.

Changing the logging preset no longer affects runtime traffic counters. It only changes emitted log records.

### Endpoint configuration

Each endpoint card supports:

- desired running state;
- bind address and UDP port;
- protocol/plugin name;
- guided protocol options;
- complete advanced `options` JSON;
- drop, duplicate and corruption rates;
- minimum/maximum response delay;
- deterministic fault seed.

Known protocols expose guided controls:

- Mitsubishi MC: accepted frames and encodings, model identity, CPU behavior, command deny lists and point limits;
- OMRON FINS/UDP: node, destination behavior and element limit;
- Modbus/UDP: accepted unit IDs and canonical memory-area mappings.

Custom plugins remain configurable through the advanced JSON editor.

Applying an endpoint configuration performs these steps:

1. validate the payload and fault values;
2. instantiate the requested protocol plugin before stopping the current endpoint;
3. check obvious active-port conflicts;
4. stop only the selected endpoint;
5. bind and start the replacement;
6. restore the previous configuration and endpoint when replacement startup fails.

The endpoint generation counter increments on every successful start, which makes restarts visible in status output.

### Endpoint actions

- **Start**: start a stopped endpoint with its current runtime configuration.
- **Stop**: stop the endpoint without stopping the web dashboard or other protocols.
- **Restart**: recreate the protocol and UDP socket with the current settings.
- **Restore startup**: restore the endpoint definition loaded from YAML and start it.
- **Reset metrics**: clear counters, latency data and recent-client history for one endpoint.
- **Reset counters**: clear telemetry for all endpoints.

## Runtime-only configuration and export

Endpoint edits are intentionally runtime-only. The source YAML is not overwritten, because rewriting YAML would destroy comments and may fail for read-only Docker mounts.

Use **Export YAML** or:

```text
GET /api/config/export
```

to download a normalized configuration containing current endpoint options, fault settings, memory configuration and logging settings.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Lightweight runtime health |
| `GET` | `/api/status` | Process, telemetry, endpoint and protocol state |
| `GET` | `/api/settings` | Editable runtime configuration and guided option schema |
| `PUT` | `/api/endpoints/{name}` | Validate, apply and restart one endpoint |
| `POST` | `/api/endpoints/{name}/action` | Start, stop, restart, restore or reset metrics |
| `POST` | `/api/metrics/reset` | Reset all endpoint telemetry |
| `POST` | `/api/logging` | Apply runtime logging settings |
| `GET` | `/api/config/export` | Download normalized runtime YAML |
| `GET` | `/api/memory` | Read shared PLC memory |
| `PUT` | `/api/memory` | Write shared PLC memory |
| `GET` | `/api/logs` | Read buffered structured logs |

Example endpoint update:

```json
{
  "running": true,
  "bind": "0.0.0.0",
  "port": 5000,
  "protocol": "mc-protocol",
  "options": {
    "accepted_frames": ["3E", "4E"],
    "accepted_encodings": ["binary"],
    "model_name": "R08CPU",
    "allow_remote_control": false
  },
  "faults": {
    "seed": 1234,
    "drop_rate": 0.01,
    "duplicate_rate": 0,
    "corrupt_rate": 0,
    "delay_ms": {"min": 5, "max": 30}
  }
}
```

Endpoint action:

```json
{"action": "restart"}
```

## Read-only mode

```bash
python main.py --no-web-write
```

Read-only mode blocks memory writes, logging changes, endpoint changes, endpoint actions and counter resets. Status, logs, memory reads and YAML export remain available.

## Security boundary

The dashboard still has no authentication or TLS. Runtime endpoint control is powerful: it can stop protocol ports, load configured plugin modules and alter fault behavior. Bind the dashboard to `127.0.0.1`, use a trusted test network, or place it behind an authenticated reverse proxy/VPN.
