"""The module's on/off settings: DHCP and NTP."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WagoConfigEntry
from .entity_descriptions import MODULE_BINARY_SENSOR_DESCRIPTIONS
from .sensor import module_device_info

# Read once at setup; nothing here updates on its own.
PARALLEL_UPDATES = 0


class WagoModuleBinarySensor(BinarySensorEntity):
    """An on/off setting of the module, as read at setup."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        description: BinarySensorEntityDescription,
        serial: str,
        device: DeviceInfo,
        value: bool,
    ) -> None:
        """Hold the value the setup read."""
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device
        self._attr_is_on = value


async def async_setup_entry(
    hass: HomeAssistant, entry: WagoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the module's switches as read at setup; nothing behind other gateways."""
    module = entry.runtime_data.module
    if module is None:
        return
    device = module_device_info(module)
    async_add_entities(
        WagoModuleBinarySensor(
            description, module["serial_number"], device, module[description.key]
        )
        for description in MODULE_BINARY_SENSOR_DESCRIPTIONS
    )
