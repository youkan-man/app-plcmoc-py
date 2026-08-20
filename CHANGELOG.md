# Changelog

## 0.2.0

- Added an auto-detecting Mitsubishi MC endpoint for A-compatible 1E and QnA-compatible 3E/4E UDP frames.
- Added binary and ASCII encoding support on the same UDP port.
- Added batch, random, monitor, multiple-block, type-name, remote-control, loopback, and error-clear command handling.
- Added standard and extended device specifications for 3E/4E subcommands `0000` through `0003`.
- Added model-profile controls for frame types, encodings, and enabled/disabled commands.
- Added validated configurable device mappings, including A-compatible 1E device codes.
- Added per-client monitor state, fail-closed validation, atomic multi-write behavior, and deterministic protocol errors.
- Expanded unit and live UDP integration tests.
