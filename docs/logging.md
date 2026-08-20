# Logging and diagnostics

The server has three independently switchable log channels:

- **application**: startup, endpoint lifecycle, shutdown, and unhandled errors;
- **traffic**: parsed request/response summaries and optional hexadecimal data;
- **memory**: canonical PLC memory writes or all reads/writes.

## Presets

```yaml
server:
  logging:
    mode: normal
```

| Mode | Application level | Traffic | Memory |
|---|---|---|---|
| `quiet` | `WARNING` | `off` | `off` |
| `normal` | `INFO` | `summary` | `off` |
| `debug` | `DEBUG` | `summary` | `write` |
| `trace` | `TRACE` | `hex` | `all` |

Individual settings override the selected preset:

```yaml
server:
  logging:
    mode: debug
    level: INFO
    format: text
    console: true
    file: ../logs/plcmock.log
    rotate_max_bytes: 10485760
    rotate_backup_count: 5
    traffic: hex
    memory: write
    max_hex_bytes: 512
    max_value_preview: 16
```

`file` is resolved relative to the YAML configuration file. When both
`console: true` and `file` are configured, the same records are written to
stderr and the rotating file.

The old flat options remain accepted:

```yaml
server:
  log_level: DEBUG
  hex_dump: true
```

## Command-line switching

The root launcher starts `config/example.yml` automatically:

```bash
python main.py
```

Useful debug invocations:

```bash
python main.py --debug
python main.py --trace
python main.py --traffic-log hex --memory-log write
python main.py --log-format json --log-file logs/plcmock.jsonl
python main.py --quiet
```

The installed CLI exposes the same options after the `serve` command:

```bash
plcmock serve --config config/example.yml --trace
```

`--trace` is a preset. A later explicit switch wins, so this enables TRACE
application logging while limiting traffic and memory output:

```bash
python main.py --trace --traffic-log summary --memory-log write
```

## Traffic summaries

MC, FINS, and Modbus frames are decoded for diagnostics independently from the
protocol handler. A normal MC summary includes the detected frame and encoding,
command, subcommand, device, address, point count, response end code, response
data size, elapsed time, endpoint, client, and request identifier.

Example shape:

```text
... INFO plcmock.traffic event=datagram_received request=mitsubishi-mc-00000001 endpoint=mitsubishi-mc protocol=mc-protocol remote=127.0.0.1:53000 MC 3E binary batch-read command=0x0401 subcommand=0x0000 device=D address=100 points=2 bytes=21
... INFO plcmock.traffic event=datagram_sent request=mitsubishi-mc-00000001 endpoint=mitsubishi-mc protocol=mc-protocol remote=127.0.0.1:53000 MC 3E binary batch-read response end=0x0000 data_bytes=4 bytes=15 duration_ms=0.412
```

The diagnostic decoder never controls protocol behavior. If it cannot parse a
packet, the handler still receives the original bytes and the log records an
`unparsed` summary.

## HEX dumps

`traffic: hex` or `--traffic-log hex` emits separate TRACE records. Dumps are
capped by `max_hex_bytes`; truncation is explicit. Setting the limit to `0`
hides byte contents while retaining parsed summaries and byte counts.

## Memory logs

`memory: write` records mutations at DEBUG. `memory: all` additionally records
reads at TRACE. Values are capped by `max_value_preview`, and every record is
correlated with the request identifier through context variables.

Initialization writes are also visible when debug or trace logging is active.
This makes unexpected startup values distinguishable from writes caused by a
PLC client.

## JSON Lines

`format: json` produces one JSON object per line. Besides the human-readable
`message`, records contain structured fields such as:

```json
{
  "event": "datagram_received",
  "request_id": "mitsubishi-mc-00000001",
  "endpoint": "mitsubishi-mc",
  "protocol": "mc-protocol",
  "remote": "127.0.0.1:53000",
  "frame": "3E",
  "encoding": "binary",
  "command": 1025,
  "command_name": "batch-read",
  "device": "D",
  "address": 100,
  "points": 2
}
```

This format is suitable for `jq`, Loki, Elasticsearch, and other log collectors.
