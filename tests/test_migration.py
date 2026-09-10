"""The YAML block's entity ids are adopted, statistics included, fail-closed.

State matrix: legacy entry present/absent x new id already registered/not x
new id held by a removed entry/not x legacy platform still live/not.
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
from custom_components.wago_879.migration import async_adopt_legacy_entities
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
    list_statistic_ids,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, EntityStateAttribute
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    area_registry as ar,
    entity_registry as er,
    label_registry as lr,
)
from homeassistant.util import dt as dt_util

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

SERIAL = "00123456"
LEGACY_PLATFORM = "modbus"
LEGACY_UNIQUE_ID = "WagoWirkenergieTotal"
LEGACY_ENTITY_ID = "sensor.wagowirkenergietotal"
NEW_UNIQUE_ID = f"{SERIAL}_active_energy_total"
# Last entry of LEGACY_UNIQUE_IDS, so a one-pass implementation would already
# have removed earlier entries before reaching the conflict this one carries.
CONFLICT_LEGACY_UNIQUE_ID = "WagoWirkenergieLieferung"
CONFLICT_ENTITY_ID = "sensor.wagowirkenergielieferung"
CONFLICT_NEW_UNIQUE_ID = f"{SERIAL}_active_energy_export"
DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 502,
    CONF_UNIT_ID: 1,
    CONF_MEASUREMENT_INTERVAL: 15,
    CONF_ENERGY_INTERVAL: 300,
}


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


def _legacy_entry(hass, unique_id=LEGACY_UNIQUE_ID, entity_id=LEGACY_ENTITY_ID):
    registry = er.async_get(hass)
    entry = registry.async_get_or_create(
        "sensor",
        LEGACY_PLATFORM,
        unique_id,
        suggested_object_id=entity_id.split(".", 1)[1],
    )
    assert entry.entity_id == entity_id
    return entry


def _entry(hass):
    # Titled the way the config flow titles an entry: with the host.
    entry = MockConfigEntry(
        domain=DOMAIN, title=DATA[CONF_HOST], data=DATA, unique_id=SERIAL
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_new_sensor_takes_the_legacy_entity_id(hass):
    _legacy_entry(hass)
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID) == (
        LEGACY_ENTITY_ID
    )
    assert (
        registry.async_get_entity_id("sensor", LEGACY_PLATFORM, LEGACY_UNIQUE_ID)
        is None
    )
    assert hass.states.get(LEGACY_ENTITY_ID).state == "1234.5"


async def test_a_renamed_legacy_entity_keeps_its_hand_picked_id(hass):
    _legacy_entry(hass, entity_id="sensor.hausanschluss_energie")
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        er.async_get(hass).async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID)
        == "sensor.hausanschluss_energie"
    )


async def test_without_legacy_entries_setup_is_a_plain_setup(hass):
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID) == (
        f"sensor.wago_879_3000_{SERIAL[-4:]}_active_energy_total"
    )


async def test_a_live_legacy_entity_refuses_setup(hass):
    """The YAML block is still loaded.

    Adopting now would strand a live entity and hand out a _2 id. Setup
    retries so the user can remove the block and restart.
    """
    _legacy_entry(hass)
    hass.states.async_set(LEGACY_ENTITY_ID, "1.0")
    entry = _entry(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", LEGACY_PLATFORM, LEGACY_UNIQUE_ID
        )
        is not None
    )


@pytest.mark.parametrize("registry_state", ["restored", "cleared", "live_unavailable"])
async def test_only_a_state_a_platform_serves_refuses_adoption(hass, registry_state):
    """A placeholder Home Assistant wrote at start is not a live YAML entity.

    On `EVENT_HOMEASSISTANT_START` Home Assistant writes an `unavailable`
    state marked `restored` for every enabled registry entry no platform
    serves, which is exactly what the documented migration leaves behind:
    remove the `modbus:` block, restart, add the integration. Refusing on that
    placeholder blocks the migration; refusing only on a non-`unavailable`
    state would let a live but unavailable YAML entity through. The three
    cases pin all of it: the placeholder is adopted, its absence is adopted,
    and an unmarked `unavailable` state is still refused.
    """
    _legacy_entry(hass)
    await hass.async_start()
    await hass.async_block_till_done()

    placeholder = hass.states.get(LEGACY_ENTITY_ID)
    assert placeholder is not None
    assert placeholder.state == STATE_UNAVAILABLE
    assert placeholder.attributes[EntityStateAttribute.RESTORED] is True

    if registry_state == "cleared":
        hass.states.async_remove(LEGACY_ENTITY_ID)
    elif registry_state == "live_unavailable":
        hass.states.async_set(LEGACY_ENTITY_ID, STATE_UNAVAILABLE)

    entry = _entry(hass)
    loaded = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    if registry_state == "live_unavailable":
        assert not loaded
        assert registry.async_get(LEGACY_ENTITY_ID).platform == LEGACY_PLATFORM
        return

    assert loaded, f"adoption refused: {entry.reason}"
    assert registry.async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID) == (
        LEGACY_ENTITY_ID
    )


async def test_adoption_is_idempotent_on_reload(hass):
    _legacy_entry(hass)
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID) == (
        LEGACY_ENTITY_ID
    )


async def test_nothing_is_removed_when_one_legacy_id_cannot_be_resolved(hass):
    """All-or-nothing: the resolution runs before the first removal.

    The conflict is one this integration can really produce: a wago_879 entry
    already claims the new unique id under a different entity id, so adopting
    would leave two registry entries claiming that one unique id.
    """
    _legacy_entry(hass)
    conflicting = _legacy_entry(hass, CONFLICT_LEGACY_UNIQUE_ID, CONFLICT_ENTITY_ID)
    registry = er.async_get(hass)
    entry = _entry(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        CONFLICT_NEW_UNIQUE_ID,
        suggested_object_id="meter_active_energy_export",
        config_entry=entry,
    )

    with pytest.raises(ConfigEntryNotReady):
        await async_adopt_legacy_entities(hass, entry, SERIAL)

    assert registry.async_get_entity_id(
        "sensor", LEGACY_PLATFORM, LEGACY_UNIQUE_ID
    ) == (LEGACY_ENTITY_ID)
    assert registry.async_get_entity_id(
        "sensor", LEGACY_PLATFORM, CONFLICT_LEGACY_UNIQUE_ID
    ) == (conflicting.entity_id)
    assert registry.async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID) is None


async def test_a_removed_entry_for_the_new_id_does_not_steal_the_entity_id(hass):
    """A removed wago_879 entry outranks `suggested_object_id` on re-creation.

    `async_get_or_create` restores a deleted entry's own entity id before it
    ever looks at `suggested_object_id`, so the refusal has to happen while the
    legacy entries are still untouched.
    """
    _legacy_entry(hass)
    conflicting = _legacy_entry(hass, CONFLICT_LEGACY_UNIQUE_ID, CONFLICT_ENTITY_ID)
    registry = er.async_get(hass)
    entry = _entry(hass)
    stale = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        CONFLICT_NEW_UNIQUE_ID,
        suggested_object_id="meter_active_energy_export",
        config_entry=entry,
    )
    registry.async_remove(stale.entity_id)

    with pytest.raises(ConfigEntryNotReady):
        await async_adopt_legacy_entities(hass, entry, SERIAL)

    assert registry.async_get_entity_id(
        "sensor", LEGACY_PLATFORM, LEGACY_UNIQUE_ID
    ) == (LEGACY_ENTITY_ID)
    assert registry.async_get_entity_id(
        "sensor", LEGACY_PLATFORM, CONFLICT_LEGACY_UNIQUE_ID
    ) == (conflicting.entity_id)


async def test_statistics_survive_the_adoption(recorder_mock, hass):
    _legacy_entry(hass)
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
            "statistic_id": LEGACY_ENTITY_ID,
            "unit_of_measurement": "kWh",
            "unit_class": "energy",
        },
        [{"start": start, "state": 1000.0, "sum": 1000.0}],
    )
    await get_instance(hass).async_block_till_done()
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await get_instance(hass).async_block_till_done()

    # Without this the test would also pass while nothing was adopted at all.
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, NEW_UNIQUE_ID) == (
        LEGACY_ENTITY_ID
    )
    ids = await get_instance(hass).async_add_executor_job(
        list_statistic_ids, hass, {LEGACY_ENTITY_ID}
    )
    assert [row["statistic_id"] for row in ids] == [LEGACY_ENTITY_ID]
    stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, LEGACY_ENTITY_ID, True, {"sum"}
    )
    assert [row["sum"] for row in stats[LEGACY_ENTITY_ID]] == [1000.0]


async def test_adoption_keeps_the_settings_the_user_made(hass):
    """Everything the user configured on the legacy entity survives adoption.

    The entity id is only half of what the owner spent years setting up: a
    renamed sensor, an icon, an area, a display precision and above all a
    deliberately disabled or hidden entity are stored on the registry entry
    that adoption removes. Losing them turns a silent migration into a visible
    regression, and a disabled sensor coming back enabled starts polling and
    recording again behind the owner's back.
    """
    legacy = _legacy_entry(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(
        legacy.entity_id,
        aliases=["house connection"],
        area_id=ar.async_get(hass).async_create("Basement").id,
        categories={"energy": "meters"},
        device_class="energy",
        disabled_by=er.RegistryEntryDisabler.USER,
        hidden_by=er.RegistryEntryHider.USER,
        icon="mdi:flash",
        labels={lr.async_get(hass).async_create("critical").label_id},
        name="Customized energy",
    )
    registry.async_update_entity_options(
        legacy.entity_id, "sensor", {"display_precision": 4}
    )
    before = registry.async_get(legacy.entity_id)
    entry = _entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    adopted = registry.async_get(LEGACY_ENTITY_ID)
    assert adopted.unique_id == NEW_UNIQUE_ID
    fields = (
        "aliases",
        "area_id",
        "categories",
        "device_class",
        "disabled_by",
        "hidden_by",
        "icon",
        "labels",
        "name",
        "options",
    )
    assert {field: getattr(adopted, field) for field in fields} == {
        field: getattr(before, field) for field in fields
    }


async def test_an_integration_disabled_legacy_entity_is_adopted_enabled(hass):
    """`disabled_by` records who disabled the entity, and only the user counts.

    The YAML modbus platform's own reason to disable an entity says nothing
    about the entity this integration creates, so carrying it over would
    disable a sensor nobody asked to disable.
    """
    legacy = _legacy_entry(hass)
    registry = er.async_get(hass)
    registry.async_update_entity(
        legacy.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
    )
    entry = _entry(hass)

    await async_adopt_legacy_entities(hass, entry, SERIAL)

    assert registry.async_get(LEGACY_ENTITY_ID).disabled_by is None
