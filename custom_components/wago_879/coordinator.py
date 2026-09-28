"""The two pollers of one meter and what a loaded entry carries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any, assert_never

from modbus_connection import ModbusError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_HOST, DOMAIN
from .entity_descriptions import Block
from .logging_policy import (
    UNREACHABLE_ERRORS,
    DeviceUnreachable,
    OfflineIsNotAnError,
    redact,
)
from .wago_879_api.device import WagoModule

_LOGGER = logging.getLogger(__name__)
# This logger is the one handed to every WagoCoordinator below, and a filter
# only sees the records written on the logger it sits on.
_LOGGER.addFilter(OfflineIsNotAnError())

# A short outage keeps the last values; entities go unavailable on the fourth
# failed poll in a row, never before the first refresh produced values.
FAILED_POLLS_TOLERATED = 3
UPDATE_TIMEOUT_SECONDS = 30

type Reader = Callable[[], Awaitable[dict[str, Any]]]


class WagoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one register block on its own interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        name: str,
        read: Reader,
        interval: timedelta,
    ) -> None:
        """Bind the block reader and the interval the entry configured."""
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=name, update_interval=interval
        )
        self._read = read
        self._failed_polls = 0
        self._host = str(entry.data.get(CONF_HOST, ""))

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT_SECONDS):
                values = await self._read()
        except (TimeoutError, ModbusError) as err:
            self._failed_polls += 1
            failure = _failure(err, self._host, self.name)
            if self.data is not None and self._failed_polls <= FAILED_POLLS_TOLERATED:
                _LOGGER.debug(
                    "%s: poll failed (%d of %d tolerated), keeping the last values: %s",
                    self.name,
                    self._failed_polls,
                    FAILED_POLLS_TOLERATED,
                    failure,
                )
                return self.data
            # `from None`: core logs the full error at debug, cause included.
            raise failure from None
        self._failed_polls = 0
        return values


def _failure(err: Exception, host: str, poller: str) -> UpdateFailed:
    """What a failed poll reports, naming the poller.

    The name is repeated in core's own "Error fetching <name> data" log line,
    but a failed first refresh becomes the entry's setup message, rebuilt from
    this translation alone - without the name it would not say which block
    failed. A bare timeout is named as such: ``str(TimeoutError())`` is empty.
    """
    if isinstance(err, TimeoutError):
        return UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="read_timed_out",
            translation_placeholders={
                "poller": poller,
                "seconds": str(UPDATE_TIMEOUT_SECONDS),
            },
        )
    error = redact(str(err), host)
    if isinstance(err, UNREACHABLE_ERRORS):
        return DeviceUnreachable(
            translation_domain=DOMAIN,
            translation_key="read_failed",
            translation_placeholders={"poller": poller, "error": error},
        )
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="read_failed",
        translation_placeholders={"poller": poller, "error": error},
    )


@dataclass
class WagoRuntimeData:
    """What a loaded entry carries."""

    serial: str
    identity: dict[str, Any]
    measurements: WagoCoordinator
    energy: WagoCoordinator
    # The 879-9000's own settings as setup read them, its registered device
    # and the handle that reads and writes it; None behind any other gateway.
    module: dict[str, Any] | None = None
    module_device_id: str | None = None
    module_api: WagoModule | None = None

    def coordinator_for(self, block: Block) -> WagoCoordinator | None:
        """The poller feeding a block; identity is read once and has none."""
        match block:
            case Block.MEASUREMENTS:
                return self.measurements
            case Block.ENERGY:
                return self.energy
            case Block.IDENTITY | Block.MODULE:
                return None
            case _:
                assert_never(block)


type WagoConfigEntry = ConfigEntry[WagoRuntimeData]
