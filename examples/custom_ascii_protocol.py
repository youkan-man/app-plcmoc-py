"""Small human-readable protocol that demonstrates the plugin API.

Commands (ASCII, one UDP datagram each):
    PING
    READW <area> <start> <count>
    WRITEW <area> <start> <value> [value ...]
    READB <area> <start> <count>
    WRITEB <area> <start> <0|1> [0|1 ...]
"""

from plcmock.memory import MemoryErrorBase
from plcmock.protocols.base import DatagramContext, ProtocolPlugin


class AsciiDemoProtocol(ProtocolPlugin):
    protocol_name = "custom-ascii"

    async def handle_datagram(self, data: bytes, context: DatagramContext) -> bytes:
        del context
        try:
            parts = data.decode("ascii").strip().split()
        except UnicodeDecodeError:
            return b"ERR ASCII\n"
        if not parts:
            return b"ERR EMPTY\n"
        command = parts[0].upper()
        try:
            if command == "PING" and len(parts) == 1:
                return b"PONG\n"
            if command == "READW" and len(parts) == 4:
                values = self.memory.word(parts[1]).read_words(int(parts[2], 0), int(parts[3], 0))
                return ("OK " + " ".join(str(value) for value in values) + "\n").encode()
            if command == "WRITEW" and len(parts) >= 4:
                self.memory.word(parts[1]).write_words(
                    int(parts[2], 0), [int(value, 0) for value in parts[3:]]
                )
                return b"OK\n"
            if command == "READB" and len(parts) == 4:
                values = self.memory.bit(parts[1]).read_bits(int(parts[2], 0), int(parts[3], 0))
                return ("OK " + " ".join("1" if value else "0" for value in values) + "\n").encode()
            if command == "WRITEB" and len(parts) >= 4:
                self.memory.bit(parts[1]).write_bits(
                    int(parts[2], 0), [int(value, 0) for value in parts[3:]]
                )
                return b"OK\n"
            return b"ERR COMMAND\n"
        except (ValueError, MemoryErrorBase) as exc:
            return f"ERR {exc}\n".encode("utf-8", "replace")
