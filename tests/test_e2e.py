"""Entry setup, entities and unload against a real Home Assistant core."""

import struct

import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from modbus_connection import ModbusConnectionError, ModbusTcpParams
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wago_879.const import (
    CONF_ENERGY_INTERVAL,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL,
    CONF_PORT,
    CONF_UNIT_ID,
    DOMAIN,
)
from custom_components.wago_879.logging_policy import mask
from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

SERIAL = "00123456"
SERIAL_TAIL = "3456"
DEVICE_NAME = f"WAGO 879-3000 {SERIAL_TAIL}"
BASE_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 502,
    CONF_UNIT_ID: 1,
    CONF_MEASUREMENT_INTERVAL: 15,
    CONF_ENERGY_INTERVAL: 300,
}


def words(value: float) -> dict[int, int]:
    raw = struct.pack(">f", value)
    return {0: int.from_bytes(raw[:2], "big"), 1: int.from_bytes(raw[2:], "big")}


def holding(values: dict[int, float]) -> dict[str, dict[int, int]]:
    out: dict[int, int] = {0x4000: 0x0012, 0x4001: 0x3456}
    for address, value in values.items():
        for offset, word in words(value).items():
            out[address + offset] = word
    return {"holding": out}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture(autouse=True)
def meter(mock_modbus):
    mock_modbus.load_raw(holding({0x5002: 230.5, 0x6000: 1234.5}))
    return mock_modbus


def _entry(hass, data=None, unique_id=SERIAL):
    entry = MockConfigEntry(
        domain=DOMAIN,
        # What the config flow titles an entry with: anything derived from the
        # title therefore carries the meter's address.
        title=BASE_DATA[CONF_HOST],
        data=data or BASE_DATA,
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_sensors_from_the_first_refresh(hass):
    entry = await _setup(hass, _entry(hass))

    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    voltage = registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_voltage_l1")
    energy = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{SERIAL}_active_energy_total"
    )
    assert hass.states.get(voltage).state == "230.5"
    assert hass.states.get(energy).state == "1234.5"


async def test_no_entity_is_named_after_the_meters_address(hass):
    """The device name, not the entry title, names every entity.

    Without a device name Home Assistant falls back to the entry title - the
    host - so both the entity id and the friendly name of all 109 sensors
    carry an address that changes whenever the meter moves.
    """
    entry = await _setup(hass, _entry(hass))

    registry = er.async_get(hass)
    voltage = registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_voltage_l1")
    assert voltage == f"sensor.wago_879_3000_{SERIAL_TAIL}_voltage_l1"
    assert hass.states.get(voltage).attributes["friendly_name"] == (
        f"{DEVICE_NAME} Voltage L1"
    )
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), entry.entry_id
    )
    assert device.name == DEVICE_NAME


async def test_the_device_carries_the_serial(hass):
    entry = await _setup(hass, _entry(hass))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), entry.entry_id
    )
    assert device is not None
    assert device.serial_number == SERIAL
    assert device.manufacturer == "WAGO"


async def test_tariff_counters_exist_but_are_disabled(hass):
    await _setup(hass, _entry(hass))
    entry = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{SERIAL}_active_energy_total_t1"
        )
    )
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_the_configured_port_reaches_the_connection(hass, meter):
    await _setup(hass, _entry(hass, {**BASE_DATA, CONF_PORT: 5020}))
    assert meter.params_seen[0].port == 5020


async def test_unload_releases_the_shared_connection(hass, meter):
    entry = await _setup(hass, _entry(hass))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not meter.connected


async def test_setup_retries_when_the_meter_does_not_answer(hass, meter):
    meter.fail_requests(ModbusConnectionError("down"))
    entry = _entry(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert BASE_DATA[CONF_HOST] not in entry.reason


async def test_setup_refuses_a_meter_with_another_serial(hass, meter):
    """A different meter at the saved address must not load under the old unique id.

    The unique id is the serial, and the entity ids are built from the serial
    that was just read: loading anyway would bind the entry - and the history
    behind its entity ids - to a different physical meter.
    """
    meter.load_raw({"holding": {0x4000: 0x0099, 0x4001: 0x8765}})
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    # The message is shown to the user AND written to the log that gets
    # attached to issue reports: it has to keep the two meters apart without
    # either serial, or the address, being readable in full.
    assert SERIAL not in entry.reason
    assert "00998765" not in entry.reason
    assert BASE_DATA[CONF_HOST] not in entry.reason
    assert mask(SERIAL) in entry.reason
    assert mask("00998765") in entry.reason
    assert mask(SERIAL) != mask("00998765")
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)


async def test_a_clash_of_link_settings_is_a_setup_error(hass):
    """No retry can resolve two consumers wanting different link settings on
    one endpoint, so the entry has to fail with the helper's own message."""
    holder = MockConfigEntry(domain="other", data={})
    holder.add_to_hass(hass)
    async_get_unit(
        hass,
        holder,
        ModbusTcpParams(
            host=BASE_DATA[CONF_HOST], port=BASE_DATA[CONF_PORT], framer="rtu"
        ),
        1,
    )

    entry = _entry(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "different link settings" in entry.reason
