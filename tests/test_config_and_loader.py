from pathlib import Path

from plcmock.config import load_config
from plcmock.memory import MemorySpace
from plcmock.protocols.loader import load_protocol


def test_example_configuration_and_custom_plugin_load() -> None:
    path = Path(__file__).parents[1] / "config" / "example.yml"
    config = load_config(path)
    memory = MemorySpace.from_config(config.memory)
    assert len(config.endpoints) == 4

    plugins = [
        load_protocol(
            endpoint.protocol,
            memory=memory,
            options=endpoint.options,
            plugin_paths=config.plugin_paths,
        )
        for endpoint in config.endpoints
    ]
    assert [plugin.protocol_name for plugin in plugins] == [
        "mc-protocol",
        "fins-udp",
        "modbus-udp",
        "custom-ascii",
    ]


def test_builtin_mc_aliases_select_expected_frame_support() -> None:
    memory = MemorySpace.from_config(
        {"words": {"D": 16}, "bits": {"M": 256}}
    )
    assert load_protocol("mc", memory=memory).protocol_name == "mc-protocol"
    assert load_protocol("mc-protocol", memory=memory).protocol_name == "mc-protocol"
    assert load_protocol("slmp", memory=memory).protocol_name == "slmp"
    assert load_protocol("slmp-3e-4e", memory=memory).protocol_name == "slmp"
    assert load_protocol("mc-1e", memory=memory).protocol_name == "mc-1e"


def test_mc_device_partial_override_is_shared_by_3e_and_1e() -> None:
    memory = MemorySpace.from_config(
        {
            "words": {"D": 16, "ALT_D": 16},
            "bits": {"M": 256},
        }
    )
    plugin = load_protocol(
        "mc-protocol",
        memory=memory,
        options={
            "device_map": {
                "0xA8": {
                    "area": "ALT_D",
                    "one_e_code": "0x4420",
                }
            }
        },
    )
    assert plugin.qna.devices[0xA8].name == "D"
    assert plugin.qna.devices[0xA8].area == "ALT_D"
    assert plugin.qna.devices[0xA8].ascii_code == "D"
    assert plugin.one_e.catalog.by_one_e[0x4420].area == "ALT_D"


def test_composite_accepts_custom_extended_slmp_device_without_1e_mapping() -> None:
    memory = MemorySpace.from_config(
        {
            "words": {"EX": 16},
            "bits": {"M": 256},
        }
    )
    plugin = load_protocol(
        "mc-protocol",
        memory=memory,
        options={
            "device_map": {
                "0x1234": {
                    "name": "EX",
                    "area": "EX",
                    "storage": "word",
                    "ascii_code": "EX",
                    "radix": 16,
                }
            }
        },
    )
    assert plugin.qna.devices[0x1234].area == "EX"
    assert 0x1234 in plugin.one_e.catalog.by_slmp
