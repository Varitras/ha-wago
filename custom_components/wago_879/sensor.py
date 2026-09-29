"""Every sensor description becomes one WagoSensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WagoConfigEntry, WagoCoordinator, WagoRuntimeData
from .entity_descriptions import (
    MODULE_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    WagoSensorDescription,
)
from .logging_policy import identifier_tail

# No sensor updates on its own: the coordinators poll, identity is read once.
PARALLEL_UPDATES = 0

MANUFACTURER = "WAGO"
MODEL = "879-3000"
MODULE_MODEL = "879-9000"


def device_name(serial: str) -> str:
    """Name the device after the meter, never after where it is plugged in.

    With `has_entity_name` this name is the first half of every entity id and
    friendly name the integration creates. Home Assistant falls back to the
    config entry title for a device without a name, and earlier versions titled
    the entry with the meter's address, so entity ids were built from an
    address that changes whenever the meter moves. The serial tail keeps two
    meters of one installation apart.

    The model is deliberately left out: it belongs on the device card, and
    naming entities after one model would age badly once this integration
    serves further WAGO meters.
    """
    return f"{MANUFACTURER} {identifier_tail(serial)}"


def entry_title(current: str, host: str, serial: str) -> str:
    """The title an entry should carry: the device name instead of an address.

    Earlier versions titled the entry with the meter's address, and core logs
    the title on every setup failure. Only a title that is exactly the
    address is replaced - a title the user chose stays.
    """
    if current == host:
        return device_name(serial)
    return current


# A float32 carries about seven significant decimal digits; printing more
# shows the binary rounding of the register (1.34 -> 1.340000033378601).
FLOAT32_SIGNIFICANT_DIGITS = 7


def _version(identity: dict[str, Any], field: str) -> str | None:
    """A version the meter did not report stays unset; `str(None)` reads as "None"."""
    value = identity.get(field)
    if value is None:
        return None
    return f"{value:.{FLOAT32_SIGNIFICANT_DIGITS}g}"


def module_device_info(module: dict[str, Any]) -> DeviceInfo:
    """The 879-9000's device card, named after its own serial's tail."""
    serial = module["serial_number"]
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=f"{MANUFACTURER} Modbus TCP {identifier_tail(serial)}",
        manufacturer=MANUFACTURER,
        model=MODULE_MODEL,
        serial_number=serial,
        sw_version=module["firmware_version"],
    )


def device_info(runtime: WagoRuntimeData) -> DeviceInfo:
    """The meter's device card, linked to the module it is reached through."""
    info = DeviceInfo(
        identifiers={(DOMAIN, runtime.serial)},
        name=device_name(runtime.serial),
        manufacturer=MANUFACTURER,
        model=MODEL,
        serial_number=runtime.serial,
        sw_version=_version(runtime.identity, "software_version"),
        hw_version=_version(runtime.identity, "hardware_version"),
    )
    if runtime.module_device_id is not None:
        info["via_device_id"] = runtime.module_device_id
    return info


class WagoPolledSensor(CoordinatorEntity[WagoCoordinator], SensorEntity):
    """A sensor fed by one of the two pollers."""

    entity_description: WagoSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WagoCoordinator,
        description: WagoSensorDescription,
        serial: str,
        device: DeviceInfo,
    ) -> None:
        """Take the first refresh's value; the listener only fires on the next poll."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device
        self._attr_native_value = coordinator.data.get(description.key)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self.coordinator.data.get(self.entity_description.key)
        self.async_write_ha_state()


class WagoStaticSensor(SensorEntity):
    """A diagnostic value read once at setup, of the meter or of the module."""

    entity_description: WagoSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: WagoSensorDescription,
        serial: str,
        device: DeviceInfo,
        values: dict[str, Any],
    ) -> None:
        """Hold the value the setup read."""
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device
        self._attr_native_value = values.get(description.key)


def _is_populated(description: WagoSensorDescription, runtime: WagoRuntimeData) -> bool:
    """Whether the meter fills the register behind `description`."""
    gate = description.populated_when_nonzero
    if gate is None:
        return True
    read_at_setup = {**runtime.identity, **(runtime.measurements.data or {})}
    return bool(read_at_setup.get(gate))


def build_entities(runtime: WagoRuntimeData) -> list[Entity]:
    """One entity per description the meter fills, on the poller its block names."""
    entities: list[Entity] = []
    meter = device_info(runtime)
    for description in SENSOR_DESCRIPTIONS:
        if not _is_populated(description, runtime):
            continue
        coordinator = runtime.coordinator_for(description.block)
        if coordinator is None:
            entities.append(
                WagoStaticSensor(description, runtime.serial, meter, runtime.identity)
            )
        else:
            entities.append(
                WagoPolledSensor(coordinator, description, runtime.serial, meter)
            )
    if runtime.module is not None:
        module = module_device_info(runtime.module)
        entities.extend(
            WagoStaticSensor(
                description, runtime.module["serial_number"], module, runtime.module
            )
            for description in MODULE_SENSOR_DESCRIPTIONS
        )
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: WagoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add every sensor; the first refresh already ran, so no update_before_add."""
    async_add_entities(build_entities(entry.runtime_data))
