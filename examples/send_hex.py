#!/usr/bin/env python3
"""Send one hexadecimal UDP datagram and print the hexadecimal response."""

import argparse
import socket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("hex_bytes", help='for example: "50 00 00 ff ff 03 ..."')
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    payload = bytes.fromhex(args.hex_bytes)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(args.timeout)
        sock.sendto(payload, (args.host, args.port))
        response, source = sock.recvfrom(65535)
    print(f"{source[0]}:{source[1]} {response.hex(' ')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
