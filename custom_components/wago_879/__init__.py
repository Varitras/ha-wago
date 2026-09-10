"""The WAGO 879 energy meter integration, on Home Assistant's shared Modbus connection."""

from __future__ import annotations

from datetime import timedelta

from modbus_connection import ModbusError, ModbusTcpParams

from homeassistant.components.modbus import async_get_unit
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from . import entity_id_rename, migration
from .const import (
    CONF_ENERGY_INTERVAL,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL,
    CONF_PORT,
    CONF_UNIT_ID,
    DEFAULT_ENERGY_INTERVAL,
    DEFAULT_MEASUREMENT_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_UNIT_ID,
)
from .coordinator import WagoConfigEntry, WagoCoordinator, WagoRuntimeData
from .logging_policy import mask
from .wago_879_api.device import WagoMeter

PLATFORMS = [Platform.SENSOR]


def _setting(entry: WagoConfigEntry, key: str, default: int) -> int:
    return int(entry.options.get(key, entry.data.get(key, default)))


async def async_setup_entry(hass: HomeAssistant, entry: WagoConfigEntry) -> bool:
    """Read the identity, adopt the YAML entities, start both pollers."""
    params = ModbusTcpParams(
        host=str(entry.data[CONF_HOST]), port=_setting(entry, CONF_PORT, DEFAULT_PORT)
    )
    try:
        unit = async_get_unit(
            hass, entry, params, _setting(entry, CONF_UNIT_ID, DEFAULT_UNIT_ID)
        )
    except HomeAssistantError as err:
        # Another entry holds this endpoint with different link settings.
        # Not ConfigEntryNotReady: no retry can resolve a clash of
        # configurations, so the user has to see the helper's own message.
        raise ConfigEntryError(str(err)) from err
    meter = WagoMeter(unit)
    try:
        identity = await meter.async_read_identity()
    except ModbusError as err:
        raise ConfigEntryNotReady(f"{mask(params.host)}: {err}") from err
    serial = meter.serial_number
    assert serial is not None
    # Before adoption, the first refresh and the platforms: the device and every
    # entity id carry the serial just read, so letting a different meter load
    # under this entry would tie that meter's readings to the other one's
    # history. Not ConfigEntryNotReady - no retry turns this into the right
    # meter. An entry without a unique id has nothing to compare against; the
    # flow always sets one, so that is only a hand-made entry.
    if entry.unique_id is not None and entry.unique_id != serial:
        raise ConfigEntryError(
            f"The meter at {mask(params.host)} answers as serial "
            f"{mask(serial)}, but this entry belongs to serial "
            f"{mask(entry.unique_id)}. Point the entry at the address of meter "
            f"{mask(entry.unique_id)} with Reconfigure, or add meter "
            f"{mask(serial)} as its own entry."
        )

    await migration.async_adopt_legacy_entities(hass, entry, serial)
    # After adoption: an adopted entity id is one of those this must not touch,
    # and it is only in the registry once adoption has put it there.
    entity_id_rename.async_rename_generated_entity_ids(hass, entry, serial)

    # The coordinator name goes into every "Error fetching %s data" line core
    # writes on an outage, so it may not be the entry title - that is the
    # meter's address. The masked serial tells the two pollers of one meter
    # apart, and two meters from each other, and never changes for a meter.
    measurements = WagoCoordinator(
        hass,
        entry,
        name=f"{mask(serial)} measurements",
        read=meter.async_update_measurements,
        interval=timedelta(
            seconds=_setting(
                entry, CONF_MEASUREMENT_INTERVAL, DEFAULT_MEASUREMENT_INTERVAL
            )
        ),
    )
    energy = WagoCoordinator(
        hass,
        entry,
        name=f"{mask(serial)} energy",
        read=meter.async_update_energy,
        interval=timedelta(
            seconds=_setting(entry, CONF_ENERGY_INTERVAL, DEFAULT_ENERGY_INTERVAL)
        ),
    )
    await measurements.async_config_entry_first_refresh()
    await energy.async_config_entry_first_refresh()

    entry.runtime_data = WagoRuntimeData(
        serial=serial,
        identity=identity,
        measurements=measurements,
        energy=energy,
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: WagoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WagoConfigEntry) -> bool:
    """Unload the platforms; the unit is released with the entry's unload hooks."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
