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
        "slmp",
        "fins-udp",
        "modbus-udp",
        "custom-ascii",
    ]
