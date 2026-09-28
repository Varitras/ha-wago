"""The diagnostics download says what the meter reports, not where or which."""

import json

import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from modbus_connection import ModbusConnectionError

from custom_components.wago_879.const import CONF_HOST
from custom_components.wago_879.diagnostics import async_get_config_entry_diagnostics
from homeassistant.config_entries import ConfigEntryState

from .test_e2e import BASE_DATA, SERIAL, _entry, _setup, holding
from .test_module import module_holding

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture(autouse=True)
def meter(mock_modbus):
    mock_modbus.load_raw(holding({0x5002: 230.5, 0x6000: 1234.5}))
    return mock_modbus


async def test_diagnostics_carry_the_readings_without_address_or_serial(hass):
    """Diagnostics are attached to issue reports, like the log."""
    entry = await _setup(hass, _entry(hass))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diagnostics)

    assert diagnostics["measurements"]["data"]["voltage_l1"] == pytest.approx(230.5)
    assert diagnostics["energy"]["data"]["active_energy_total"] == pytest.approx(1234.5)
    assert diagnostics["measurements"]["last_update_success"] is True
    assert BASE_DATA[CONF_HOST] not in text
    assert SERIAL not in text
    # The identity block holds the serial as the meter sends it: a number.
    assert str(int(SERIAL, 16)) not in text


async def test_diagnostics_of_an_entry_that_is_not_loaded(hass, mock_modbus):
    """A retrying entry is exactly the one a user asks about; it has no
    runtime data, and the download may not fail for lack of it."""
    mock_modbus.fail_requests(ModbusConnectionError("down"))
    entry = _entry(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["state"] == ConfigEntryState.SETUP_RETRY.value
    assert "measurements" not in diagnostics
    assert BASE_DATA[CONF_HOST] not in json.dumps(diagnostics)


async def test_diagnostics_carry_the_module_without_its_addresses(hass, meter):
    """The module's settings name the network it sits in; the shape of its
    configuration is what helps a report, not the addresses."""
    raw = module_holding()
    # A distinct address in every server word, so each one's redaction counts.
    servers = {0x006B: 21, 0x006D: 22, 0x006F: 23, 0x0071: 24}
    for address, last in servers.items():
        raw["holding"].update({address: 0xC000, address + 1: 0x0200 | last})
    meter.load_module_raw(raw)
    entry = await _setup(hass, _entry(hass))

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diagnostics)

    assert diagnostics["module"]["baud_rate"] == 115200
    assert diagnostics["module"]["ntp"] is True
    identifying = [
        "192.0.2.4",
        "192.0.2.1",
        *(f"192.0.2.{last}" for last in servers.values()),
        "Wago-TCP",
        "033000000001",
    ]
    for value in identifying:
        assert value not in text, value
