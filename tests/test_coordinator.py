"""A short outage keeps the last values; a long one takes the entities down."""

import asyncio
from datetime import timedelta
import logging

from modbus_connection import IllegalDataAddressError, ModbusConnectionError
import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wago_879 import coordinator as coordinator_module
from custom_components.wago_879.const import DOMAIN
from custom_components.wago_879.coordinator import (
    FAILED_POLLS_TOLERATED,
    WagoCoordinator,
    WagoRuntimeData,
)
from custom_components.wago_879.entity_descriptions import Block
from homeassistant.helpers.update_coordinator import UpdateFailed


class Reader:
    def __init__(self):
        self.fail = False
        self.error = ModbusConnectionError("down")
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.fail:
            raise self.error
        return {"voltage_l1": 230.0}


@pytest.fixture
def coordinator(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    reader = Reader()
    coordinator = WagoCoordinator(
        hass, entry, name="test", read=reader, interval=timedelta(seconds=15)
    )
    return coordinator, reader


async def test_first_refresh_delivers_values(coordinator):
    poller, _ = coordinator
    await poller.async_refresh()
    assert poller.data == {"voltage_l1": 230.0}


async def test_tolerated_failures_keep_the_last_values(coordinator):
    poller, reader = coordinator
    await poller.async_refresh()
    reader.fail = True
    for _ in range(FAILED_POLLS_TOLERATED):
        await poller.async_refresh()
        assert poller.last_update_success is True
        assert poller.data == {"voltage_l1": 230.0}


async def test_one_failure_too_many_marks_the_refresh_failed(coordinator):
    poller, reader = coordinator
    await poller.async_refresh()
    reader.fail = True
    for _ in range(FAILED_POLLS_TOLERATED + 1):
        await poller.async_refresh()
    assert poller.last_update_success is False


async def test_a_failure_before_any_value_is_not_tolerated(coordinator):
    poller, reader = coordinator
    reader.fail = True
    with pytest.raises(UpdateFailed):
        await poller._async_update_data()


async def test_a_good_poll_resets_the_count(coordinator):
    poller, reader = coordinator
    await poller.async_refresh()
    reader.fail = True
    await poller.async_refresh()
    reader.fail = False
    await poller.async_refresh()
    reader.fail = True
    for _ in range(FAILED_POLLS_TOLERATED):
        await poller.async_refresh()
    assert poller.last_update_success is True


async def test_three_tolerated_then_a_fourth_fails(coordinator):
    """FAILED_POLLS_TOLERATED is an operational promise, not an implementation
    detail: it is how long a gateway hiccup may last before the meter's
    entities go unavailable. Stated in literal numbers so a change to the
    constant is caught here, not just re-derived by the rest of the suite.
    """
    poller, reader = coordinator
    await poller.async_refresh()
    reader.fail = True
    await poller.async_refresh()
    assert poller.last_update_success is True
    await poller.async_refresh()
    assert poller.last_update_success is True
    await poller.async_refresh()
    assert poller.last_update_success is True
    assert poller.data == {"voltage_l1": 230.0}
    await poller.async_refresh()
    assert poller.last_update_success is False


class HangingReader:
    """A reader that, once armed, blocks well past the (patched) timeout."""

    def __init__(self):
        self.hang = False
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.hang:
            await asyncio.sleep(3600)
        return {"voltage_l1": 230.0}


@pytest.fixture
def hanging_coordinator(hass, monkeypatch):
    monkeypatch.setattr(coordinator_module, "UPDATE_TIMEOUT_SECONDS", 0.01)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    reader = HangingReader()
    poller = WagoCoordinator(
        hass, entry, name="test", read=reader, interval=timedelta(seconds=15)
    )
    return poller, reader


async def test_a_slow_read_is_tolerated_like_any_other_failed_poll(
    hanging_coordinator,
):
    poller, reader = hanging_coordinator
    await poller.async_refresh()
    reader.hang = True
    for _ in range(FAILED_POLLS_TOLERATED):
        await poller.async_refresh()
        assert poller.last_update_success is True
        assert poller.data == {"voltage_l1": 230.0}
    await poller.async_refresh()
    assert poller.last_update_success is False


async def test_a_slow_read_before_any_value_raises_with_timeout_wording(
    hanging_coordinator,
):
    poller, reader = hanging_coordinator
    reader.hang = True
    with pytest.raises(UpdateFailed, match="timed out after 0.01s"):
        await poller._async_update_data()


def test_coordinator_for_maps_each_block_to_its_own_poller():
    measurements = object()
    energy = object()
    runtime_data = WagoRuntimeData(
        serial="00123456",
        identity={},
        measurements=measurements,
        energy=energy,
    )
    assert runtime_data.coordinator_for(Block.MEASUREMENTS) is measurements
    assert runtime_data.coordinator_for(Block.ENERGY) is energy
    assert runtime_data.coordinator_for(Block.IDENTITY) is None


def _fetch_failure_levels(caplog):
    """The level of every record core wrote for a failed refresh."""
    return [
        record.levelno
        for record in caplog.records
        if str(record.msg).startswith("Error fetching")
    ]


async def test_an_unreachable_meter_is_not_logged_as_an_error(coordinator, caplog):
    """A meter that is switched off is not something the user can fix, and
    `log-when-unavailable` puts that on info; core hard-codes error."""
    poller, reader = coordinator
    reader.fail = True

    with caplog.at_level(logging.INFO):
        await poller.async_refresh()

    assert _fetch_failure_levels(caplog) == [logging.INFO]


async def test_a_refused_read_is_still_an_error(coordinator, caplog):
    """The counter-check: a meter that answers and refuses the block is a real
    fault, and a filter that downgraded it too would downgrade everything."""
    poller, reader = coordinator
    reader.fail = True
    reader.error = IllegalDataAddressError()

    with caplog.at_level(logging.INFO):
        await poller.async_refresh()

    assert _fetch_failure_levels(caplog) == [logging.ERROR]


async def test_a_slow_read_is_still_an_error(hanging_coordinator, caplog):
    """A cycle that ran out of time may well have a reachable meter behind it."""
    poller, reader = hanging_coordinator
    reader.hang = True

    with caplog.at_level(logging.INFO):
        await poller.async_refresh()

    assert _fetch_failure_levels(caplog) == [logging.ERROR]
