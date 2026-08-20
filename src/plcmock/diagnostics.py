from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class DatagramDescription:
    summary: str
    fields: dict[str, Any]


MC_COMMANDS: dict[int, str] = {
    0x0101: "read-type-name",
    0x0401: "batch-read",
    0x1401: "batch-write",
    0x0403: "random-read",
    0x1402: "random-write",
    0x0801: "monitor-register",
    0x0802: "monitor-execute",
    0x0406: "block-read",
    0x1406: "block-write",
    0x1001: "remote-run",
    0x1002: "remote-stop",
    0x1003: "remote-pause",
    0x1005: "remote-latch-clear",
    0x1006: "remote-reset",
    0x0619: "loopback",
    0x1617: "clear-error",
}
MC_1E_COMMANDS: dict[int, str] = {
    0x00: "batch-read-bit",
    0x01: "batch-read-word",
    0x02: "batch-write-bit",
    0x03: "batch-write-word",
    0x04: "random-write-bit",
    0x05: "random-write-word",
    0x06: "monitor-register-bit",
    0x07: "monitor-register-word",
    0x08: "monitor-execute-bit",
    0x09: "monitor-execute-word",
}
FINS_COMMANDS: dict[int, str] = {
    0x0101: "memory-area-read",
    0x0102: "memory-area-write",
}
MODBUS_FUNCTIONS: dict[int, str] = {
    0x01: "read-coils",
    0x02: "read-discrete-inputs",
    0x03: "read-holding-registers",
    0x04: "read-input-registers",
    0x05: "write-single-coil",
    0x06: "write-single-register",
    0x0F: "write-multiple-coils",
    0x10: "write-multiple-registers",
}


def describe_request(plugin: Any, data: bytes) -> DatagramDescription:
    protocol = str(getattr(plugin, "protocol_name", plugin.__class__.__name__))
    try:
        if protocol in {"mc-protocol", "slmp", "mc-1e"}:
            return _describe_mc_request(plugin, data)
        if protocol == "fins-udp":
            return _describe_fins_request(plugin, data)
        if protocol == "modbus-udp":
            return _describe_modbus_request(data)
        return DatagramDescription(
            f"{protocol} datagram",
            {"protocol_family": protocol, "payload_bytes": len(data)},
        )
    except Exception as exc:
        return DatagramDescription(
            f"{protocol} unparsed datagram bytes={len(data)}",
            {
                "protocol_family": protocol,
                "payload_bytes": len(data),
                "diagnostic_error": f"{type(exc).__name__}: {exc}",
            },
        )


def describe_response(
    plugin: Any,
    request: bytes,
    response: bytes,
) -> DatagramDescription:
    protocol = str(getattr(plugin, "protocol_name", plugin.__class__.__name__))
    try:
        if protocol in {"mc-protocol", "slmp", "mc-1e"}:
            return _describe_mc_response(plugin, request, response)
        if protocol == "fins-udp":
            return _describe_fins_response(request, response)
        if protocol == "modbus-udp":
            return _describe_modbus_response(request, response)
        return DatagramDescription(
            f"{protocol} response",
            {"protocol_family": protocol, "payload_bytes": len(response)},
        )
    except Exception as exc:
        return DatagramDescription(
            f"{protocol} unparsed response bytes={len(response)}",
            {
                "protocol_family": protocol,
                "payload_bytes": len(response),
                "diagnostic_error": f"{type(exc).__name__}: {exc}",
            },
        )


def _describe_mc_request(plugin: Any, data: bytes) -> DatagramDescription:
    kind = _mc_frame_kind(data)
    if kind is None:
        return DatagramDescription(
            f"MC unknown frame bytes={len(data)}",
            {"protocol_family": "mc", "payload_bytes": len(data)},
        )
    frame, encoding, response = kind
    if response:
        return DatagramDescription(
            f"MC {frame} {encoding} response-like request bytes={len(data)}",
            {
                "protocol_family": "mc",
                "frame": frame,
                "encoding": encoding,
                "payload_bytes": len(data),
            },
        )
    if frame == "1E":
        return _describe_mc_1e_request(plugin, data, encoding)
    return _describe_mc_qna_request(plugin, data, frame, encoding)


def _describe_mc_response(
    plugin: Any, request: bytes, response: bytes
) -> DatagramDescription:
    request_description = _describe_mc_request(plugin, request)
    fields = dict(request_description.fields)
    kind = _mc_frame_kind(response)
    if kind is None:
        fields["response_bytes"] = len(response)
        return DatagramDescription(
            f"{request_description.summary} response bytes={len(response)}",
            fields,
        )
    frame, encoding, _ = kind
    end_code: int | None = None
    data_bytes = 0
    if frame == "1E":
        if encoding == "binary" and len(response) >= 2:
            end_code = response[1]
            data_bytes = max(0, len(response) - 2)
        elif encoding == "ascii" and len(response) >= 4:
            end_code = int(response[2:4], 16)
            data_bytes = max(0, len(response) - 4)
    elif encoding == "binary":
        body_offset = 9 if frame == "3E" else 13
        if len(response) >= body_offset + 2:
            end_code = int.from_bytes(response[body_offset : body_offset + 2], "little")
            data_bytes = len(response) - body_offset - 2
    else:
        body_offset = 18 if frame == "3E" else 26
        if len(response) >= body_offset + 4:
            end_code = int(response[body_offset : body_offset + 4], 16)
            data_bytes = len(response) - body_offset - 4
    fields.update(
        {
            "response_frame": frame,
            "response_encoding": encoding,
            "end_code": end_code,
            "response_data_bytes": data_bytes,
            "response_bytes": len(response),
        }
    )
    command = fields.get("command")
    if command in {0x1001, 0x1002, 0x1003, 0x1005, 0x1006, 0x1617}:
        qna = getattr(plugin, "qna", getattr(plugin, "slmp", plugin))
        for attribute in ("cpu_state", "last_clear_mode", "error_code"):
            if hasattr(qna, attribute):
                fields[attribute] = getattr(qna, attribute)
    end = "unknown" if end_code is None else f"0x{end_code:04X}"
    command_name = fields.get("command_name", "command")
    state = f" state={fields['cpu_state']}" if "cpu_state" in fields else ""
    return DatagramDescription(
        f"MC {frame} {encoding} {command_name} response end={end} data_bytes={data_bytes}{state}",
        fields,
    )


def _describe_mc_qna_request(
    plugin: Any,
    data: bytes,
    frame: str,
    encoding: str,
) -> DatagramDescription:
    if encoding == "binary":
        body_offset = 9 if frame == "3E" else 13
        if len(data) < body_offset + 6:
            return _partial_mc(frame, encoding, len(data))
        body = data[body_offset:]
        command = int.from_bytes(body[2:4], "little")
        subcommand = int.from_bytes(body[4:6], "little")
        payload = body[6:]
    else:
        body_offset = 18 if frame == "3E" else 26
        if len(data) < body_offset + 12:
            return _partial_mc(frame, encoding, len(data))
        command = int(data[body_offset + 4 : body_offset + 8], 16)
        subcommand = int(data[body_offset + 8 : body_offset + 12], 16)
        payload = data[body_offset + 12 :]
    command_name = MC_COMMANDS.get(command, "unknown-command")
    fields: dict[str, Any] = {
        "protocol_family": "mc",
        "frame": frame,
        "encoding": encoding,
        "command": command,
        "command_hex": f"0x{command:04X}",
        "command_name": command_name,
        "subcommand": subcommand,
        "subcommand_hex": f"0x{subcommand:04X}",
        "payload_bytes": len(data),
    }
    details: list[str] = []
    if command in (0x0401, 0x1401):
        target = _qna_batch_target(plugin, payload, subcommand, encoding)
        if target:
            fields.update(target)
            details.append(
                f"device={target['device']} address={target['address']} points={target['points']}"
            )
    elif command in (0x0403, 0x0801):
        counts = _qna_pair_counts(payload, encoding)
        if counts:
            fields.update({"word_points": counts[0], "dword_points": counts[1]})
            details.append(f"words={counts[0]} dwords={counts[1]}")
    elif command == 0x1402:
        counts = _qna_random_write_counts(payload, subcommand, encoding)
        fields.update(counts)
        if "bit_points" in counts:
            details.append(f"bits={counts['bit_points']}")
        elif counts:
            details.append(
                f"words={counts.get('word_points', '?')} dwords={counts.get('dword_points', '?')}"
            )
    elif command in (0x0406, 0x1406):
        counts = _qna_pair_counts(payload, encoding)
        if counts:
            fields.update({"word_blocks": counts[0], "bit_blocks": counts[1]})
            details.append(f"word_blocks={counts[0]} bit_blocks={counts[1]}")
    summary = (
        f"MC {frame} {encoding} {command_name} command=0x{command:04X} "
        f"subcommand=0x{subcommand:04X}"
    )
    if details:
        summary += " " + " ".join(details)
    return DatagramDescription(summary, fields)


def _describe_mc_1e_request(
    plugin: Any, data: bytes, encoding: str
) -> DatagramDescription:
    if encoding == "binary":
        if len(data) < 4:
            return _partial_mc("1E", encoding, len(data))
        command = data[0]
        pc_number = data[1]
        payload = data[4:]
    else:
        if len(data) < 8:
            return _partial_mc("1E", encoding, len(data))
        command = int(data[:2], 16)
        pc_number = int(data[2:4], 16)
        payload = data[8:]
    command_name = MC_1E_COMMANDS.get(command, "unknown-command")
    fields: dict[str, Any] = {
        "protocol_family": "mc",
        "frame": "1E",
        "encoding": encoding,
        "command": command,
        "command_hex": f"0x{command:02X}",
        "command_name": command_name,
        "pc_number": pc_number,
        "payload_bytes": len(data),
    }
    details: list[str] = []
    if command in (0x00, 0x01, 0x02, 0x03):
        target = _one_e_batch_target(plugin, payload, encoding)
        if target:
            fields.update(target)
            details.append(
                f"device={target['device']} address={target['address']} points={target['points']}"
            )
    elif command in (0x04, 0x05, 0x06, 0x07):
        count = _one_e_count(payload, encoding)
        if count is not None:
            fields["points"] = count
            details.append(f"points={count}")
    summary = f"MC 1E {encoding} {command_name} command=0x{command:02X} pc=0x{pc_number:02X}"
    if details:
        summary += " " + " ".join(details)
    return DatagramDescription(summary, fields)


def _describe_fins_request(plugin: Any, data: bytes) -> DatagramDescription:
    if len(data) < 12:
        return DatagramDescription(
            f"FINS/UDP partial request bytes={len(data)}",
            {"protocol_family": "fins", "payload_bytes": len(data)},
        )
    command = int.from_bytes(data[10:12], "big")
    command_name = FINS_COMMANDS.get(command, "unknown-command")
    fields: dict[str, Any] = {
        "protocol_family": "fins",
        "command": command,
        "command_hex": f"0x{command:04X}",
        "command_name": command_name,
        "sid": data[9],
        "payload_bytes": len(data),
    }
    details: list[str] = []
    if command in (0x0101, 0x0102) and len(data) >= 18:
        area_code = data[12]
        address = int.from_bytes(data[13:15], "big")
        bit = data[15]
        points = int.from_bytes(data[16:18], "big")
        area = _fins_area_name(plugin, area_code)
        fields.update(
            {
                "area_code": area_code,
                "area": area,
                "address": address,
                "bit": bit,
                "points": points,
            }
        )
        details.append(f"area={area} address={address}.{bit} points={points}")
    summary = f"FINS/UDP {command_name} command=0x{command:04X} sid={data[9]}"
    if details:
        summary += " " + " ".join(details)
    return DatagramDescription(summary, fields)


def _describe_fins_response(request: bytes, response: bytes) -> DatagramDescription:
    request_command = int.from_bytes(request[10:12], "big") if len(request) >= 12 else None
    command_name = FINS_COMMANDS.get(request_command or -1, "command")
    end_code = int.from_bytes(response[12:14], "big") if len(response) >= 14 else None
    data_bytes = max(0, len(response) - 14)
    end = "unknown" if end_code is None else f"0x{end_code:04X}"
    return DatagramDescription(
        f"FINS/UDP {command_name} response end={end} data_bytes={data_bytes}",
        {
            "protocol_family": "fins",
            "command": request_command,
            "command_name": command_name,
            "end_code": end_code,
            "response_data_bytes": data_bytes,
            "response_bytes": len(response),
        },
    )


def _describe_modbus_request(data: bytes) -> DatagramDescription:
    if len(data) < 8:
        return DatagramDescription(
            f"Modbus/UDP partial request bytes={len(data)}",
            {"protocol_family": "modbus", "payload_bytes": len(data)},
        )
    transaction = int.from_bytes(data[0:2], "big")
    protocol_id = int.from_bytes(data[2:4], "big")
    unit = data[6]
    function = data[7]
    function_name = MODBUS_FUNCTIONS.get(function, "unknown-function")
    fields: dict[str, Any] = {
        "protocol_family": "modbus",
        "transaction_id": transaction,
        "protocol_id": protocol_id,
        "unit_id": unit,
        "function": function,
        "function_hex": f"0x{function:02X}",
        "function_name": function_name,
        "payload_bytes": len(data),
    }
    details: list[str] = []
    if len(data) >= 12 and function in (0x01, 0x02, 0x03, 0x04, 0x0F, 0x10):
        address = int.from_bytes(data[8:10], "big")
        points = int.from_bytes(data[10:12], "big")
        fields.update({"address": address, "points": points})
        details.append(f"address={address} points={points}")
    elif len(data) >= 12 and function in (0x05, 0x06):
        address = int.from_bytes(data[8:10], "big")
        value = int.from_bytes(data[10:12], "big")
        fields.update({"address": address, "value": value})
        details.append(f"address={address} value=0x{value:04X}")
    summary = (
        f"Modbus/UDP {function_name} function=0x{function:02X} "
        f"transaction={transaction} unit={unit}"
    )
    if details:
        summary += " " + " ".join(details)
    return DatagramDescription(summary, fields)


def _describe_modbus_response(request: bytes, response: bytes) -> DatagramDescription:
    transaction = int.from_bytes(response[0:2], "big") if len(response) >= 2 else None
    unit = response[6] if len(response) >= 7 else None
    function = response[7] if len(response) >= 8 else None
    request_function = request[7] if len(request) >= 8 else None
    function_name = MODBUS_FUNCTIONS.get(request_function or -1, "function")
    exception = None
    if function is not None and function & 0x80 and len(response) >= 9:
        exception = response[8]
    data_bytes = max(0, len(response) - 8)
    suffix = (
        f" exception=0x{exception:02X}"
        if exception is not None
        else f" data_bytes={data_bytes}"
    )
    return DatagramDescription(
        f"Modbus/UDP {function_name} response transaction={transaction} unit={unit}{suffix}",
        {
            "protocol_family": "modbus",
            "transaction_id": transaction,
            "unit_id": unit,
            "function": request_function,
            "function_name": function_name,
            "exception_code": exception,
            "response_data_bytes": data_bytes,
            "response_bytes": len(response),
        },
    )


def _mc_frame_kind(data: bytes) -> tuple[str, str, bool] | None:
    if len(data) >= 2:
        if data[:2] == b"\x50\x00":
            return "3E", "binary", False
        if data[:2] == b"\x54\x00":
            return "4E", "binary", False
        if data[:2] == b"\xD0\x00":
            return "3E", "binary", True
        if data[:2] == b"\xD4\x00":
            return "4E", "binary", True
    if len(data) >= 4:
        prefix = data[:4].upper()
        if prefix == b"5000":
            return "3E", "ascii", False
        if prefix == b"5400":
            return "4E", "ascii", False
        if prefix == b"D000":
            return "3E", "ascii", True
        if prefix == b"D400":
            return "4E", "ascii", True
    if len(data) >= 2 and _is_hex(data[:2]):
        command = int(data[:2], 16)
        if 0x80 <= command <= 0x89 and len(data) >= 4:
            return "1E", "ascii", True
        if 0 <= command <= 0x09 and len(data) >= 8:
            return "1E", "ascii", False
    if len(data) >= 2 and (0 <= data[0] <= 0x09 or 0x80 <= data[0] <= 0x89):
        return "1E", "binary", data[0] >= 0x80
    return None


def _qna_batch_target(
    plugin: Any, payload: bytes, subcommand: int, encoding: str
) -> dict[str, Any] | None:
    extended = bool(subcommand & 2)
    if encoding == "binary":
        address_width = 4 if extended else 3
        code_width = 2 if extended else 1
        needed = address_width + code_width + 2
        if len(payload) < needed:
            return None
        address = int.from_bytes(payload[:address_width], "little")
        code = int.from_bytes(
            payload[address_width : address_width + code_width], "little"
        )
        points = int.from_bytes(payload[address_width + code_width : needed], "little")
        device = _qna_device_name(plugin, code=code)
    else:
        code_width = 4 if extended else 2
        address_width = 8 if extended else 6
        needed = code_width + address_width + 4
        if len(payload) < needed:
            return None
        raw_code = payload[:code_width].decode("ascii", "replace")
        raw_address = payload[code_width : code_width + address_width].decode(
            "ascii", "replace"
        )
        spec = _qna_device_spec(plugin, ascii_code=raw_code)
        radix = int(getattr(spec, "radix", 10)) if spec is not None else 10
        address = int(raw_address.replace(" ", "0"), radix)
        points = int(payload[code_width + address_width : needed], 16)
        device = str(getattr(spec, "name", raw_code.rstrip("* ")))
    return {
        "device": device,
        "device_code": code if encoding == "binary" else raw_code,
        "address": address,
        "points": points,
        "unit": "bit" if subcommand & 1 else "word",
    }


def _one_e_batch_target(
    plugin: Any, payload: bytes, encoding: str
) -> dict[str, Any] | None:
    if encoding == "binary":
        if len(payload) < 8:
            return None
        address = int.from_bytes(payload[:4], "little")
        code = int.from_bytes(payload[4:6], "little")
        raw_count = payload[6]
    else:
        if len(payload) < 16:
            return None
        code = int(payload[:4], 16)
        address = int(payload[4:12], 16)
        raw_count = int(payload[12:14], 16)
    spec = _one_e_device_spec(plugin, code)
    return {
        "device": str(getattr(spec, "name", f"0x{code:04X}")),
        "device_code": code,
        "address": address,
        "points": 256 if raw_count == 0 else raw_count,
    }


def _one_e_count(payload: bytes, encoding: str) -> int | None:
    if encoding == "binary":
        if len(payload) < 1:
            return None
        raw = payload[0]
    else:
        if len(payload) < 2:
            return None
        raw = int(payload[:2], 16)
    return 256 if raw == 0 else raw


def _qna_pair_counts(payload: bytes, encoding: str) -> tuple[int, int] | None:
    if encoding == "binary":
        if len(payload) < 2:
            return None
        return payload[0], payload[1]
    if len(payload) < 4:
        return None
    return int(payload[:2], 16), int(payload[2:4], 16)


def _qna_random_write_counts(
    payload: bytes, subcommand: int, encoding: str
) -> dict[str, int]:
    if subcommand & 1:
        if encoding == "binary":
            return {"bit_points": payload[0]} if payload else {}
        return {"bit_points": int(payload[:2], 16)} if len(payload) >= 2 else {}
    counts = _qna_pair_counts(payload, encoding)
    if counts is None:
        return {}
    return {"word_points": counts[0], "dword_points": counts[1]}


def _qna_device_spec(
    plugin: Any,
    *,
    code: int | None = None,
    ascii_code: str | None = None,
) -> Any | None:
    qna = getattr(plugin, "qna", getattr(plugin, "slmp", plugin))
    if code is not None:
        devices = getattr(qna, "devices", {})
        if isinstance(devices, Mapping):
            return devices.get(code)
    if ascii_code is not None:
        normalized = ascii_code.upper().rstrip("* ")
        ascii_devices = getattr(qna, "ascii_devices", {})
        if isinstance(ascii_devices, Mapping):
            return ascii_devices.get(normalized)
    return None


def _qna_device_name(plugin: Any, *, code: int) -> str:
    spec = _qna_device_spec(plugin, code=code)
    return str(getattr(spec, "name", f"0x{code:04X}"))


def _one_e_device_spec(plugin: Any, code: int) -> Any | None:
    one_e = getattr(plugin, "one_e", plugin)
    catalog = getattr(one_e, "catalog", None)
    table = getattr(catalog, "by_one_e", {})
    if isinstance(table, Mapping):
        return table.get(code)
    return None


def _fins_area_name(plugin: Any, code: int) -> str:
    areas = getattr(plugin, "areas", {})
    spec = areas.get(code) if isinstance(areas, Mapping) else None
    return str(getattr(spec, "name", f"0x{code:02X}"))


def _partial_mc(frame: str, encoding: str, size: int) -> DatagramDescription:
    return DatagramDescription(
        f"MC {frame} {encoding} partial request bytes={size}",
        {
            "protocol_family": "mc",
            "frame": frame,
            "encoding": encoding,
            "payload_bytes": size,
        },
    )


def _is_hex(data: bytes) -> bool:
    return bool(data) and all(value in b"0123456789ABCDEFabcdef" for value in data)
