"""Entity ids built from the old host-named device move onto the device name.

Everything else stays where it is: an adopted YAML id, an id the user renamed
by hand, an id already on the new pattern, and an id whose target is taken.
"""

from datetime import timedelta
import struct

import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wago_879.const import (
    CONF_ENERGY_INTERVAL,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL,
    CONF_PORT,
    CONF_UNIT_ID,
    DOMAIN,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    list_statistic_ids,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

SERIAL = "00123456"
HOST = "192.0.2.10"
DATA = {
    CONF_HOST: HOST,
    CONF_PORT: 502,
    CONF_UNIT_ID: 1,
    CONF_MEASUREMENT_INTERVAL: 15,
    CONF_ENERGY_INTERVAL: 300,
}

FREQUENCY_UNIQUE_ID = f"{SERIAL}_frequency"
FREQUENCY_NAME = "Frequency"
# What the device without a name produced: the entry title is the host.
HOST_ENTITY_ID = "sensor.192_0_2_10_frequency"
DEVICE_ENTITY_ID = "sensor.wago_879_3000_3456_frequency"

ENERGY_UNIQUE_ID = f"{SERIAL}_active_energy_total"
ENERGY_NAME = "Active energy total"
ADOPTED_ENTITY_ID = "sensor.wagowirkenergietotal"


def _holding() -> dict[str, dict[int, int]]:
    raw = struct.pack(">f", 1234.5)
    return {
        "holding": {
            0x4000: 0x0012,
            0x4001: 0x3456,
            0x6000: int.from_bytes(raw[:2], "big"),
            0x6001: int.from_bytes(raw[2:], "big"),
        }
    }


@pytest.fixture(autouse=True)
def _hass_prerequisites(recorder_db_url, enable_custom_integrations):
    # `recorder_db_url` asserts that `hass` has not been set up yet, while
    # `enable_custom_integrations` pulls `hass` in. Requesting both from one
    # fixture in this order is what pins the database url to come first.
    return


@pytest.fixture(autouse=True)
def meter(mock_modbus):
    mock_modbus.load_raw(_holding())
    return mock_modbus


def _entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, title=HOST, data=DATA, unique_id=SERIAL)
    entry.add_to_hass(hass)
    return entry


def _existing_entity(hass, entry, unique_id, object_id, name):
    """A registry entry as an earlier version of this integration left it."""
    registry = er.async_get(hass)
    created = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        has_entity_name=True,
        original_name=name,
        suggested_object_id=object_id,
    )
    assert created.entity_id == f"sensor.{object_id}"
    return created


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass, unique_id) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


async def test_an_id_built_from_the_host_moves_to_the_device_name(hass):
    entry = _entry(hass)
    _existing_entity(
        hass,
        entry,
        FREQUENCY_UNIQUE_ID,
        HOST_ENTITY_ID.split(".", 1)[1],
        FREQUENCY_NAME,
    )

    await _setup(hass, entry)

    assert _entity_id(hass, FREQUENCY_UNIQUE_ID) == DEVICE_ENTITY_ID
    assert hass.states.get(HOST_ENTITY_ID) is None


async def test_statistics_survive_the_rename(recorder_mock, hass):
    entry = _entry(hass)
    _existing_entity(
        hass, entry, ENERGY_UNIQUE_ID, "192_0_2_10_active_energy_total", ENERGY_NAME
    )
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=2
    )
    async_import_statistics(
        hass,
        {
            "has_mean": False,
            "has_sum": True,
            "mean_type": StatisticMeanType.NONE,
            "name": None,
            "source": "recorder",
            "statistic_id": "sensor.192_0_2_10_active_energy_total",
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
        },
        [{"start": start, "state": 1000.0, "sum": 1000.0}],
    )
    await get_instance(hass).async_block_till_done()

    await _setup(hass, entry)
    await get_instance(hass).async_block_till_done()

    renamed = "sensor.wago_879_3000_3456_active_energy_total"
    # Without this the test would also pass while nothing was renamed at all.
    assert _entity_id(hass, ENERGY_UNIQUE_ID) == renamed
    ids = await get_instance(hass).async_add_executor_job(
        list_statistic_ids, hass, {renamed}
    )
    assert [row["statistic_id"] for row in ids] == [renamed]
    stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, renamed, True, {"sum"}
    )
    assert [row["sum"] for row in stats[renamed]] == [1000.0]


async def test_an_adopted_id_keeps_its_name(hass):
    """The thirteen ids adopted from the YAML block carry years of history
    under a name the user knows; they never matched the host pattern."""
    entry = _entry(hass)
    _existing_entity(
        hass, entry, ENERGY_UNIQUE_ID, ADOPTED_ENTITY_ID.split(".", 1)[1], ENERGY_NAME
    )

    await _setup(hass, entry)

    assert _entity_id(hass, ENERGY_UNIQUE_ID) == ADOPTED_ENTITY_ID


async def test_an_id_the_user_renamed_by_hand_is_left_alone(hass):
    entry = _entry(hass)
    _existing_entity(
        hass, entry, FREQUENCY_UNIQUE_ID, "cellar_meter_frequency", FREQUENCY_NAME
    )

    await _setup(hass, entry)

    assert _entity_id(hass, FREQUENCY_UNIQUE_ID) == "sensor.cellar_meter_frequency"


async def test_a_second_setup_renames_nothing(hass):
    entry = _entry(hass)
    _existing_entity(
        hass,
        entry,
        FREQUENCY_UNIQUE_ID,
        HOST_ENTITY_ID.split(".", 1)[1],
        FREQUENCY_NAME,
    )
    await _setup(hass, entry)
    assert _entity_id(hass, FREQUENCY_UNIQUE_ID) == DEVICE_ENTITY_ID

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # A second pass over an already renamed id would collide with itself and
    # land on a `_2` suffix.
    assert _entity_id(hass, FREQUENCY_UNIQUE_ID) == DEVICE_ENTITY_ID


async def test_a_taken_target_id_skips_that_entity_and_loads_anyway(hass):
    """One rename that cannot happen is no reason to leave the meter unloadable."""
    entry = _entry(hass)
    _existing_entity(
        hass,
        entry,
        FREQUENCY_UNIQUE_ID,
        HOST_ENTITY_ID.split(".", 1)[1],
        FREQUENCY_NAME,
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        "other_integration",
        "squatter",
        suggested_object_id=DEVICE_ENTITY_ID.split(".", 1)[1],
    )

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert _entity_id(hass, FREQUENCY_UNIQUE_ID) == HOST_ENTITY_ID
