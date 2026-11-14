from unittest.mock import patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def skip_bluetooth_dependencies():
    """Don't start HA's real Bluetooth stack in tests.

    The tests patch the bluetooth helpers they use, and the real stack tries
    to open sockets (blocked by pytest-socket) on CI runners.
    """
    from homeassistant.loader import Integration

    def deps(self):
        return [
            d
            for d in self.manifest.get("dependencies", [])
            if d not in ("bluetooth", "bluetooth_adapters")
        ]

    with patch.object(Integration, "dependencies", property(deps)):
        yield
