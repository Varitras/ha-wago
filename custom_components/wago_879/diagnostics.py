"""The diagnostics download: what the meter reports, without where or which.

It is attached to issue reports like the log, so the address and the serial
number are redacted - the device name in the title keeps the serial tail,
which is enough to tell two meters apart.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_HOST
from .coordinator import WagoConfigEntry, WagoCoordinator

TO_REDACT = {CONF_HOST, "serial_number", "unique_id"}


def _poller(coordinator: WagoCoordinator) -> dict[str, Any]:
    return {
        "update_interval_seconds": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
        "last_update_success": coordinator.last_update_success,
        "data": coordinator.data,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WagoConfigEntry
) -> dict[str, Any]:
    """Entry settings, the identity read at setup, and both pollers."""
    runtime = entry.runtime_data
    return async_redact_data(
        {
            "entry": {
                "title": entry.title,
                "unique_id": entry.unique_id,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "identity": runtime.identity,
            "measurements": _poller(runtime.measurements),
            "energy": _poller(runtime.energy),
        },
        TO_REDACT,
    )
