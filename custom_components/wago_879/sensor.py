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
from .entity_descriptions import SENSOR_DESCRIPTIONS, WagoSensorDescription
from .logging_policy import identifier_tail

MANUFACTURER = "WAGO"
MODEL = "879-3000"


def device_name(serial: str) -> str:
    """Name the device after the meter, never after where it is plugged in.

    With `has_entity_name` this name is the first half of every entity id and
    friendly name the integration creates. Home Assistant falls back to the config entry title for a device without a
    name, and that title is the meter's address, so entity ids would be built
    from an address that changes whenever the meter moves. The serial tail
    keeps two meters of one installation apart.
    """
    return f"{MANUFACTURER} {MODEL} {identifier_tail(serial)}"


def _version(identity: dict[str, Any], field: str) -> str | None:
    """A version the meter did not report stays unset; `str(None)` reads as "None"."""
    value = identity.get(field)
    return None if value is None else str(value)


def device_info(serial: str, identity: dict[str, Any]) -> DeviceInfo:
    """The device card: serial, firmware and hardware version."""
    return DeviceInfo(
        identifiers={(DOMAIN, serial)},
        name=device_name(serial),
        manufacturer=MANUFACTURER,
        model=MODEL,
        serial_number=serial,
        sw_version=_version(identity, "software_version"),
        hw_version=_version(identity, "hardware_version"),
    )


class WagoPolledSensor(CoordinatorEntity[WagoCoordinator], SensorEntity):
    """A sensor fed by one of the two pollers."""

    entity_description: WagoSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WagoCoordinator,
        description: WagoSensorDescription,
        serial: str,
        identity: dict[str, Any],
    ) -> None:
        """Take the first refresh's value; the listener only fires on the next poll."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info(serial, identity)
        self._attr_native_value = coordinator.data.get(description.key)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self.coordinator.data.get(self.entity_description.key)
        self.async_write_ha_state()


class WagoIdentitySensor(SensorEntity):
    """A diagnostic value read once at setup."""

    entity_description: WagoSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, description: WagoSensorDescription, serial: str, identity: dict[str, Any]
    ) -> None:
        """Hold the value the setup read."""
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info(serial, identity)
        self._attr_native_value = identity.get(description.key)


def build_entities(runtime: WagoRuntimeData) -> list[Entity]:
    """One entity per description, on the poller its block names."""
    entities: list[Entity] = []
    for description in SENSOR_DESCRIPTIONS:
        coordinator = runtime.coordinator_for(description.block)
        if coordinator is None:
            entities.append(
                WagoIdentitySensor(description, runtime.serial, runtime.identity)
            )
        else:
            entities.append(
                WagoPolledSensor(
                    coordinator, description, runtime.serial, runtime.identity
                )
            )
    return entities


async def async_setup_entry(
    hass: HomeAssistant, entry: WagoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add every sensor; the first refresh already ran, so no update_before_add."""
    async_add_entities(build_entities(entry.runtime_data))
