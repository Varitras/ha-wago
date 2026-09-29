"""The diagnostics download: what the meter reports, without where or which.

It is attached to issue reports like the log, so the address and the serial
number are redacted - the device name in the title keeps the serial tail,
which is enough to tell two meters apart.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import CONF_HOST
from .coordinator import WagoConfigEntry, WagoCoordinator
from .wago_879_api.device import MODULE_SERVERS

# The module's settings name the network it sits in; its serial identifies it.
TO_REDACT = {
    CONF_HOST,
    "serial_number",
    "unique_id",
    "ip_address",
    "gateway",
    "hostname",
    *MODULE_SERVERS,
}


def _poller(coordinator: WagoCoordinator) -> dict[str, Any]:
    return {
        "update_interval": str(coordinator.update_interval),
        "last_update_success": coordinator.last_update_success,
        "data": coordinator.data,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WagoConfigEntry
) -> dict[str, Any]:
    """Entry settings, and once loaded the identity and both pollers."""
    diagnostics: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "state": entry.state.value,
            "unique_id": entry.unique_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        }
    }
    # Only a loaded entry has runtime data - and a retrying one is exactly
    # the entry a user downloads diagnostics for.
    if entry.state is ConfigEntryState.LOADED:
        runtime = entry.runtime_data
        diagnostics["identity"] = runtime.identity
        diagnostics["measurements"] = _poller(runtime.measurements)
        diagnostics["energy"] = _poller(runtime.energy)
        diagnostics["module"] = runtime.module
    return async_redact_data(diagnostics, TO_REDACT)
