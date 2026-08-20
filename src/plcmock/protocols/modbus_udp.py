from __future__ import annotations

import struct
from typing import Any, Mapping

from plcmock.memory import AddressOutOfRange, InvalidMemoryValue, UnknownArea

from .base import DatagramContext, ProtocolPlugin


class ModbusUdpProtocol(ProtocolPlugin):
    """Modbus Application Data Units carried in UDP datagrams.

    This intentionally reuses the Modbus TCP MBAP/PDU shape without a TCP
    stream. It is a pragmatic compatibility extension, not Modbus TCP.
    """

    protocol_name = "modbus-udp"

    ILLEGAL_FUNCTION = 0x01
    ILLEGAL_DATA_ADDRESS = 0x02
    ILLEGAL_DATA_VALUE = 0x03
    SERVER_DEVICE_FAILURE = 0x04

    def __init__(self, memory, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(memory, options)
        areas = self.options.get("areas", {})
        if not isinstance(areas, Mapping):
            raise ValueError("modbus.options.areas must be a mapping")
        self.coils = str(areas.get("coils", "M"))
        self.discrete_inputs = str(areas.get("discrete_inputs", "X"))
        self.holding_registers = str(areas.get("holding_registers", "D"))
        self.input_registers = str(areas.get("input_registers", "INPUT"))
        accepted = self.options.get("accepted_unit_ids")
        if accepted is None:
            self.accepted_unit_ids: set[int] | None = None
        else:
            if not isinstance(accepted, list) or any(
                isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 255
                for item in accepted
            ):
                raise ValueError("accepted_unit_ids must be a list of bytes")
            self.accepted_unit_ids = set(accepted)

    async def handle_datagram(self, data: bytes, context: DatagramContext) -> bytes | None:
        del context
        if len(data) < 8:
            return None
        transaction_id, protocol_id, length = struct.unpack(">HHH", data[:6])
        if protocol_id != 0 or length < 2 or len(data) != 6 + length:
            return None
        unit_id = data[6]
        function = data[7]
        payload = data[8:]
        broadcast = unit_id == 0
        if not broadcast and self.accepted_unit_ids is not None and unit_id not in self.accepted_unit_ids:
            return None

        try:
            response_pdu = self._dispatch(function, payload)
        except UnknownArea:
            # UnknownArea inherits ValueError, so it must be handled before the
            # generic invalid-value branch. A missing configured memory area is
            # a server configuration/device failure, not bad client data.
            response_pdu = self._exception(function, self.SERVER_DEVICE_FAILURE)
        except AddressOutOfRange:
            response_pdu = self._exception(function, self.ILLEGAL_DATA_ADDRESS)
        except (InvalidMemoryValue, ValueError, struct.error):
            response_pdu = self._exception(function, self.ILLEGAL_DATA_VALUE)

        if broadcast:
            return None
        response_length = len(response_pdu) + 1
        return struct.pack(">HHHB", transaction_id, 0, response_length, unit_id) + response_pdu

    def _dispatch(self, function: int, payload: bytes) -> bytes:
        if function in (0x01, 0x02):
            return self._read_bits(function, payload)
        if function in (0x03, 0x04):
            return self._read_registers(function, payload)
        if function == 0x05:
            return self._write_single_coil(payload)
        if function == 0x06:
            return self._write_single_register(payload)
        if function == 0x0F:
            return self._write_multiple_coils(payload)
        if function == 0x10:
            return self._write_multiple_registers(payload)
        return self._exception(function, self.ILLEGAL_FUNCTION)

    def _read_bits(self, function: int, payload: bytes) -> bytes:
        if len(payload) != 4:
            return self._exception(function, self.ILLEGAL_DATA_VALUE)
        address, count = struct.unpack(">HH", payload)
        if not 1 <= count <= 2000:
            return self._exception(function, self.ILLEGAL_DATA_VALUE)
        area = self.memory.bit(self.coils if function == 0x01 else self.discrete_inputs)
        bits = area.read_bits(address, count)
        packed = bytearray((count + 7) // 8)
        for index, enabled in enumerate(bits):
            if enabled:
                packed[index // 8] |= 1 << (index % 8)
        return bytes([function, len(packed)]) + bytes(packed)

    def _read_registers(self, function: int, payload: bytes) -> bytes:
        if len(payload) != 4:
            return self._exception(function, self.ILLEGAL_DATA_VALUE)
        address, count = struct.unpack(">HH", payload)
        if not 1 <= count <= 125:
            return self._exception(function, self.ILLEGAL_DATA_VALUE)
        area = self.memory.word(
            self.holding_registers if function == 0x03 else self.input_registers
        )
        values = area.read_words(address, count)
        encoded = struct.pack(f">{len(values)}H", *values)
        return bytes([function, len(encoded)]) + encoded

    def _write_single_coil(self, payload: bytes) -> bytes:
        if len(payload) != 4:
            return self._exception(0x05, self.ILLEGAL_DATA_VALUE)
        address, value = struct.unpack(">HH", payload)
        if value not in (0x0000, 0xFF00):
            return self._exception(0x05, self.ILLEGAL_DATA_VALUE)
        self.memory.bit(self.coils).write_bits(address, [value == 0xFF00])
        return bytes([0x05]) + payload

    def _write_single_register(self, payload: bytes) -> bytes:
        if len(payload) != 4:
            return self._exception(0x06, self.ILLEGAL_DATA_VALUE)
        address, value = struct.unpack(">HH", payload)
        self.memory.word(self.holding_registers).write_words(address, [value])
        return bytes([0x06]) + payload

    def _write_multiple_coils(self, payload: bytes) -> bytes:
        if len(payload) < 5:
            return self._exception(0x0F, self.ILLEGAL_DATA_VALUE)
        address, count, byte_count = struct.unpack(">HHB", payload[:5])
        if not 1 <= count <= 1968 or byte_count != (count + 7) // 8 or len(payload) != 5 + byte_count:
            return self._exception(0x0F, self.ILLEGAL_DATA_VALUE)
        data = payload[5:]
        values = [bool((data[index // 8] >> (index % 8)) & 1) for index in range(count)]
        self.memory.bit(self.coils).write_bits(address, values)
        return bytes([0x0F]) + struct.pack(">HH", address, count)

    def _write_multiple_registers(self, payload: bytes) -> bytes:
        if len(payload) < 5:
            return self._exception(0x10, self.ILLEGAL_DATA_VALUE)
        address, count, byte_count = struct.unpack(">HHB", payload[:5])
        if not 1 <= count <= 123 or byte_count != count * 2 or len(payload) != 5 + byte_count:
            return self._exception(0x10, self.ILLEGAL_DATA_VALUE)
        values = struct.unpack(f">{count}H", payload[5:])
        self.memory.word(self.holding_registers).write_words(address, values)
        return bytes([0x10]) + struct.pack(">HH", address, count)

    @staticmethod
    def _exception(function: int, code: int) -> bytes:
        return bytes([function | 0x80, code])
