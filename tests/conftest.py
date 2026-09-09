"""Shared pytest configuration.

Declares the Home Assistant custom-component test plugin (the `hass` fixture
and a matching Home Assistant install) and keeps every test off real hardware.
"""

import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")
from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection

pytest_plugins = ("pytest_homeassistant_custom_component",)

# Where Home Assistant's modbus integration builds the connection it shares;
# the mock goes in there, so the whole path from async_get_unit down is real.
CORE_CONNECTION = "homeassistant.components.modbus.connection.ModbusConnection"


@pytest.fixture(autouse=True)
def _no_real_meter(monkeypatch):
    """Nothing in the suite may open a TCP connection to a meter.

    Global on purpose: a protection whose absence is silent belongs where every
    module gets it. A test that wants a meter asks for `mock_modbus`.
    """

    def _refuse(params, **_kwargs):
        raise AssertionError(
            "a test reached a real Modbus device - use the mock_modbus fixture"
        )

    monkeypatch.setattr(CORE_CONNECTION, _refuse)


class SharedMockModbus:
    """Stands in for the core modbus integration's connection factory.

    The hub closes the shared connection when the last entry lets go of it
    and builds a new one on the next load, so every call hands out a fresh
    in-memory connection - seeded with the same registers and failures, the
    way the real meter is still the same meter after a reload.
    """

    def __init__(self) -> None:
        self.params_seen: list = []
        self.connections: list[MockModbusConnection] = []
        self._raw: dict = {"holding": {}}
        self._request_failure: Exception | None = None
        self._read_failures: list = []

    def __call__(self, params, **_kwargs) -> MockModbusConnection:
        self.params_seen.append(params)
        connection = MockModbusConnection()
        unit = connection.for_unit(1)
        unit.load_raw(self._raw)
        unit.fail_requests(self._request_failure)
        for address, error in self._read_failures:
            unit.fail_read(address, error, register_type="holding")
        self.connections.append(connection)
        return connection

    @property
    def unit(self):
        """The unit of the connection currently handed out."""
        return self.connections[-1].for_unit(1)

    @property
    def connected(self) -> bool:
        return bool(self.connections) and self.connections[-1].connected

    def load_raw(self, raw: dict) -> None:
        for space, values in raw.items():
            self._raw[space].update(values)
        for connection in self.connections:
            connection.for_unit(1).load_raw(raw)

    def fail_read_band(self, address: int) -> None:
        """The meter refuses the holding block starting at ``address``."""
        self._read_failures.append((address, IllegalDataAddressError()))
        for connection in self.connections:
            connection.for_unit(1).fail_read(
                address, IllegalDataAddressError(), register_type="holding"
            )

    def fail_requests(self, error: Exception | None) -> None:
        self._request_failure = error
        for connection in self.connections:
            connection.for_unit(1).fail_requests(error)


@pytest.fixture
def mock_modbus(monkeypatch):
    """The connection the core modbus integration hands out, in memory.

    `params_seen` records every connection the integration asked for - one
    per (re)load, since the last unit released closes the shared one.
    """
    shared = SharedMockModbus()
    monkeypatch.setattr(CORE_CONNECTION, shared)
    return shared
