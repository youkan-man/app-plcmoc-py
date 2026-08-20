#!/usr/bin/env python3
"""Source-tree launcher for app-plcmoc-py.

Running ``python main.py`` starts the example configuration without requiring an
editable installation. All normal ``plcmock serve`` logging switches are also
accepted directly.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plcmock.cli import main as cli_main  # noqa: E402


DEFAULT_CONFIG = ROOT / "config" / "example.yml"
COMMANDS = {"serve", "check"}


def normalize_argv(argv: list[str]) -> list[str]:
    args = list(argv)
    if not args:
        return ["serve", "--config", str(DEFAULT_CONFIG)]

    if args[0] in COMMANDS:
        command, rest = args[0], args[1:]
        if not _has_config_option(rest):
            rest = ["--config", str(DEFAULT_CONFIG), *rest]
        return [command, *rest]

    # Options supplied without an explicit subcommand are treated as serve
    # options, making ``python main.py --trace`` the convenient debug path.
    if _has_config_option(args):
        return ["serve", *args]
    return ["serve", "--config", str(DEFAULT_CONFIG), *args]


def main(argv: list[str] | None = None) -> int:
    return cli_main(normalize_argv(list(sys.argv[1:] if argv is None else argv)))


def _has_config_option(args: list[str]) -> bool:
    for index, value in enumerate(args):
        if value in {"--config", "-c"}:
            return index + 1 < len(args)
        if value.startswith("--config="):
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
