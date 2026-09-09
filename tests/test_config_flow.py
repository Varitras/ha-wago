"""The config, reconfigure and options flows through the flow manager."""

import pytest

pytest.importorskip("pytest_homeassistant_custom_component.common")

from modbus_connection import ModbusConnectionError, ModbusTcpParams
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wago_879.const import (
    CONF_ENERGY_INTERVAL,
    CONF_HOST,
    CONF_MEASUREMENT_INTERVAL,
    CONF_PORT,
    CONF_UNIT_ID,
    DOMAIN,
)
from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]

HOST = "192.0.2.10"
SERIAL = "00123456"
USER_INPUT = {
    CONF_HOST: HOST,
    CONF_PORT: 502,
    CONF_UNIT_ID: 1,
    CONF_MEASUREMENT_INTERVAL: 15,
    CONF_ENERGY_INTERVAL: 300,
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture(autouse=True)
def meter(mock_modbus):
    mock_modbus.load_raw({"holding": {0x4000: 0x0012, 0x4001: 0x3456}})
    return mock_modbus


async def _start(hass, source="user"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source}
    )
    assert result["type"] is FlowResultType.FORM
    return result


async def test_the_user_step_creates_an_entry_keyed_by_serial(hass):
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == HOST
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == SERIAL


async def test_a_meter_that_does_not_answer_is_an_error(hass, meter):
    meter.fail_requests(ModbusConnectionError("down"))
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_clash_of_link_settings_is_a_form_error(hass):
    """A clash has to reach the user as a form error, not as a raised exception.

    Its remedy differs from cannot_connect: the address answers, but another
    consumer holds it with link settings that cannot both be honoured.
    """
    holder = MockConfigEntry(domain="other", data={})
    holder.add_to_hass(hass)
    async_get_unit(hass, holder, ModbusTcpParams(host=HOST, framer="rtu"), 1)

    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "link_settings_clash"}


async def test_a_blank_host_is_an_error(hass):
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**USER_INPUT, CONF_HOST: " "}
    )
    assert result["errors"] == {"base": "invalid_host"}


async def test_a_host_with_surrounding_blanks_is_accepted_and_trimmed(hass):
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**USER_INPUT, CONF_HOST: f" {HOST} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == HOST


async def test_the_same_meter_twice_aborts(hass):
    MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL).add_to_hass(hass)
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_change_the_intervals(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MEASUREMENT_INTERVAL: 30, CONF_ENERGY_INTERVAL: 600}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_MEASUREMENT_INTERVAL: 30, CONF_ENERGY_INTERVAL: 600}


async def test_reconfigure_updates_the_host_and_reloads_once(hass, meter):
    """The entry is set up first: only a loaded entry carries the update
    listener, and a listener plus a scheduled reload would rebuild the Modbus
    unit twice - which core reports and drops in 2026.12."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    connections_before = len(meter.params_seen)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.0.2.11", CONF_PORT: 502, CONF_UNIT_ID: 1}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.0.2.11"
    # The flow's probe opens one connection and the single reload the update
    # listener performs opens the second; a second reload would make three.
    assert len(meter.params_seen) == connections_before + 2


async def _reconfigure(hass, entry, host="192.0.2.11"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: host, CONF_PORT: 502, CONF_UNIT_ID: 1}
    )


async def test_reconfigure_loads_an_entry_that_was_never_set_up(hass):
    """An entry with no update listener still has to be reloaded by the flow.

    The listener is registered by a successful setup only, so without an
    explicit reload the flow would report success on an entry that stays
    unloaded.
    """
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry)
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_recovers_an_entry_stuck_in_setup_error(hass):
    """Pointing a failed entry at a working address has to bring it back up.

    A setup error leaves no update listener behind, and recovering from one is
    the reason a user reaches for reconfigure in the first place.
    """
    holder = MockConfigEntry(domain="other", data={})
    holder.add_to_hass(hass)
    async_get_unit(hass, holder, ModbusTcpParams(host=HOST, framer="rtu"), 1)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    result = await _reconfigure(hass, entry)
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_against_a_different_meter_does_not_rebind_the_entry(
    hass, mock_modbus
):
    """A reconfigure must refuse a different meter, keeping entry history intact.

    Rebinding silently would attach this entry - and every entity's recorded
    history - to a different physical meter answering on the new address.
    """
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    mock_modbus.load_raw({"holding": {0x4000: 0x0099, 0x4001: 0x8765}})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.0.2.11", CONF_PORT: 502, CONF_UNIT_ID: 1}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data == USER_INPUT
    assert entry.unique_id == SERIAL
