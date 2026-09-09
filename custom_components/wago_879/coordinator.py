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

from .entity_descriptions import Block
from .logging_policy import UNREACHABLE_ERRORS, DeviceUnreachable, OfflineIsNotAnError

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

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT_SECONDS):
                values = await self._read()
        except (TimeoutError, ModbusError) as err:
            self._failed_polls += 1
            failure = _describe_failure(err)
            if self.data is not None and self._failed_polls <= FAILED_POLLS_TOLERATED:
                _LOGGER.debug(
                    "%s: poll failed (%d of %d tolerated), keeping the last values: %s",
                    self.name,
                    self._failed_polls,
                    FAILED_POLLS_TOLERATED,
                    failure,
                )
                return self.data
            failed = (
                DeviceUnreachable
                if isinstance(err, UNREACHABLE_ERRORS)
                else UpdateFailed
            )
            raise failed(f"{self.name}: {failure}") from err
        self._failed_polls = 0
        return values


def _describe_failure(err: Exception) -> str:
    """Name a bare timeout as such; ``str(TimeoutError())`` is empty and unhelpful."""
    if isinstance(err, TimeoutError):
        return f"Modbus read timed out after {UPDATE_TIMEOUT_SECONDS}s"
    return f"Modbus read failed: {err}"


@dataclass
class WagoRuntimeData:
    """What a loaded entry carries."""

    serial: str
    identity: dict[str, Any]
    measurements: WagoCoordinator
    energy: WagoCoordinator

    def coordinator_for(self, block: Block) -> WagoCoordinator | None:
        """The poller feeding a block; identity is read once and has none."""
        match block:
            case Block.MEASUREMENTS:
                return self.measurements
            case Block.ENERGY:
                return self.energy
            case Block.IDENTITY:
                return None
            case _:
                assert_never(block)


type WagoConfigEntry = ConfigEntry[WagoRuntimeData]
