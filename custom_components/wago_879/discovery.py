"""Search Home Assistant's subnets for 879-9000 modules."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
import logging

from homeassistant.components import network
from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .wago_879_api.discovery import FoundModule, async_browse

# Long enough to stay out of the way, short enough that a module given a new
# address by DHCP is followed before anyone goes looking for its readings.
DISCOVERY_INTERVAL = timedelta(minutes=15)
_LOGGER = logging.getLogger(__name__)


async def async_find_modules(hass: HomeAssistant) -> list[FoundModule]:
    """The modules in Home Assistant's subnets; none when the search cannot run."""
    targets = [
        str(address)
        for address in await network.async_get_ipv4_broadcast_addresses(hass)
    ]
    try:
        return await async_browse(targets)
    except OSError as err:
        _LOGGER.debug("Module search did not run: %s", err)
        return []


@callback
def async_start_discovery(hass: HomeAssistant) -> None:
    """Search now and then every DISCOVERY_INTERVAL; each find opens a flow."""

    async def _async_search(_now: datetime | None = None) -> None:
        for module in await async_find_modules(hass):
            discovery_flow.async_create_flow(
                hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data=asdict(module),
            )

    hass.async_create_background_task(_async_search(), f"{DOMAIN} module search")
    async_track_time_interval(
        hass, _async_search, DISCOVERY_INTERVAL, cancel_on_shutdown=True
    )
