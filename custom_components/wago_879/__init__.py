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
from homeassistant.helpers import issue_registry as ir

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
    DOMAIN,
    INTERVAL_FIELDS,
    INTERVALS_IN_OPTIONS_MINOR_VERSION,
)
from .coordinator import WagoConfigEntry, WagoCoordinator, WagoRuntimeData
from .logging_policy import mask, redact
from .sensor import entry_title
from .wago_879_api.device import UnsupportedMeter, WagoMeter

PLATFORMS = [Platform.SENSOR]


def _adoption_blocked(entry: WagoConfigEntry) -> str:
    """The repair issue a refused adoption raises for `entry`."""
    return f"adoption_blocked_{entry.entry_id}"


def _setting(entry: WagoConfigEntry, key: str, default: int) -> int:
    return int(entry.options.get(key, entry.data.get(key, default)))


async def async_setup_entry(hass: HomeAssistant, entry: WagoConfigEntry) -> bool:
    """Read the identity, adopt the YAML entities, start both pollers."""
    # An issue describes the attempt that raised it. Cleared before anything
    # can fail, so a later attempt that stops earlier - the meter offline, say
    # - does not leave it claiming an obstacle that may be gone.
    ir.async_delete_issue(hass, DOMAIN, _adoption_blocked(entry))
    host = str(entry.data[CONF_HOST])
    # Before anything can fail: core logs the title on every setup failure.
    # Keyed on the serial the entry belongs to, not on one read just now.
    if entry.unique_id is not None:
        hass.config_entries.async_update_entry(
            entry, title=entry_title(entry.title, host, entry.unique_id)
        )
    params = ModbusTcpParams(host=host, port=_setting(entry, CONF_PORT, DEFAULT_PORT))
    try:
        unit = async_get_unit(
            hass, entry, params, _setting(entry, CONF_UNIT_ID, DEFAULT_UNIT_ID)
        )
    except HomeAssistantError as err:
        # Another entry holds this endpoint with different link settings.
        # Not ConfigEntryNotReady: no retry can resolve a clash of
        # configurations, so the user has to see the helper's own message.
        # `from None` here and below: core writes the setup error with the
        # full traceback, and a chained cause would carry the unmasked text.
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="link_settings_clash",
            translation_placeholders={"error": redact(str(err), host)},
        ) from None
    meter = WagoMeter(unit)
    try:
        identity = await meter.async_read_identity()
    except ModbusError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="meter_not_answering",
            translation_placeholders={
                "host": mask(host),
                "error": redact(str(err), host),
            },
        ) from None
    except UnsupportedMeter as err:
        # Not ConfigEntryNotReady: no retry turns another device into this meter.
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_meter",
            translation_placeholders={"meter_code": f"0x{err.meter_code:04X}"},
        ) from None
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
            translation_domain=DOMAIN,
            translation_key="serial_mismatch",
            translation_placeholders={
                "host": mask(host),
                "found": mask(serial),
                "expected": mask(entry.unique_id),
            },
        )

    # A refused adoption waits for the user, and a retrying entry is easy to
    # miss: the repairs panel is where Home Assistant asks for that. The
    # issue carries the refusal's own translation key and placeholders.
    try:
        await migration.async_adopt_legacy_entities(hass, entry, serial)
    except ConfigEntryNotReady as err:
        # Every refusal in migration.py is translated (test_translations pins
        # it); the check only narrows the type.
        if err.translation_key is not None:
            ir.async_create_issue(
                hass,
                DOMAIN,
                _adoption_blocked(entry),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=err.translation_key,
                translation_placeholders=err.translation_placeholders,
            )
        raise
    # After adoption: an adopted entity id is one of those this must not touch,
    # and it is only in the registry once adoption has put it there.
    entity_id_rename.async_rename_generated_entity_ids(hass, entry, serial)

    # The coordinator name goes into every "Error fetching %s data" line core
    # writes on an outage, so it may not be the entry title - the user can
    # rename that to anything. The masked serial tells the two pollers of one
    # meter apart, and two meters from each other, and never changes.
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


async def async_remove_entry(hass: HomeAssistant, entry: WagoConfigEntry) -> None:
    """A deleted entry has nothing left to repair."""
    ir.async_delete_issue(hass, DOMAIN, _adoption_blocked(entry))


async def async_migrate_entry(hass: HomeAssistant, entry: WagoConfigEntry) -> bool:
    """1.1 -> 1.2: the poll intervals move from data to options.

    Options set since win - they are what the user chose last.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < INTERVALS_IN_OPTIONS_MINOR_VERSION:
        data = dict(entry.data)
        moved = {key: data.pop(key) for key in INTERVAL_FIELDS if key in data}
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options={**moved, **entry.options},
            minor_version=INTERVALS_IN_OPTIONS_MINOR_VERSION,
        )
    return True
