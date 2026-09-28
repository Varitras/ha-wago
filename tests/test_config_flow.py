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
    DEFAULT_ENERGY_INTERVAL,
    DEFAULT_MEASUREMENT_INTERVAL,
    DOMAIN,
)
from custom_components.wago_879.sensor import MODULE_MODEL
from custom_components.wago_879.wago_879_api.discovery import FoundModule
from custom_components.wago_879.wago_879_api.registers import MODULE_UNIT_ID
from homeassistant.components import network
from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import NumberSelector, SelectSelector, TextSelector

from .test_module import module_holding

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
    mock_modbus.load_raw({"holding": {0x4000: 0x0012, 0x4001: 0x3456, 0x4002: 0x1111}})
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
    assert result["title"] == "WAGO 3456"
    # The quality scale's config-flow rule: what the connection needs goes to
    # data, every other setting to options.
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 502, CONF_UNIT_ID: 1}
    assert result["options"] == {
        CONF_MEASUREMENT_INTERVAL: 15,
        CONF_ENERGY_INTERVAL: 300,
    }
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
    entry = MockConfigEntry(
        domain=DOMAIN, title="my meter", data=USER_INPUT, unique_id=SERIAL
    )
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
    # A title the user chose is theirs; only an address is ever retitled.
    assert entry.title == "my meter"
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
    mock_modbus.load_raw({"holding": {0x4000: 0x0099, 0x4001: 0x8765, 0x4002: 0x1111}})

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


async def test_the_form_uses_selectors(hass):
    """The quality scale's config-flow rule asks for the right selector per
    field: a number box with its range, not a bare text field."""
    started = await _start(hass)
    fields = {str(key): value for key, value in started["data_schema"].schema.items()}

    assert isinstance(fields[CONF_HOST], TextSelector)
    for number in (
        CONF_PORT,
        CONF_UNIT_ID,
        CONF_MEASUREMENT_INTERVAL,
        CONF_ENERGY_INTERVAL,
    ):
        assert isinstance(fields[number], NumberSelector), number


async def test_numbers_from_the_form_are_stored_as_integers(hass):
    """A number selector hands over floats; a port of 502.0 is no port."""
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"],
        {
            **USER_INPUT,
            CONF_PORT: 502.0,
            CONF_UNIT_ID: 1.0,
            CONF_MEASUREMENT_INTERVAL: 15.0,
            CONF_ENERGY_INTERVAL: 300.0,
        },
    )

    stored = {**result["data"], **result["options"]}
    assert all(type(stored[key]) is int for key in stored if key != CONF_HOST)


async def test_an_entry_from_an_earlier_version_moves_its_intervals_to_options(hass):
    """Version 1.1 entries kept the intervals in data; options set later win."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={CONF_ENERGY_INTERVAL: 600},
        unique_id=SERIAL,
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert entry.data == {CONF_HOST: HOST, CONF_PORT: 502, CONF_UNIT_ID: 1}
    assert entry.options == {CONF_MEASUREMENT_INTERVAL: 15, CONF_ENERGY_INTERVAL: 600}


@pytest.mark.parametrize(
    "bad", [{CONF_PORT: 502.5}, {CONF_UNIT_ID: "nan"}, {CONF_ENERGY_INTERVAL: 300.5}]
)
async def test_a_number_that_is_not_whole_is_refused_not_rounded(hass, bad):
    """The selector's step=1 binds the UI only; the flow API takes any number,
    and 502.5 silently stored as 502 is a setting nobody entered."""
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {**USER_INPUT, **bad}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_a_whole_number"}


async def test_the_options_refuse_a_number_that_is_not_whole(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MEASUREMENT_INTERVAL: 30.5, CONF_ENERGY_INTERVAL: 600},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_a_whole_number"}
    assert entry.options == {}


async def test_a_device_that_is_no_wago_879_is_refused(hass, meter):
    meter.load_raw({"holding": {0x4002: 0x9999}})
    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_meter"}


async def test_reconfigure_retitles_an_entry_still_named_after_its_old_address(hass):
    """Setup retitles an address title only while it equals the saved host. A
    disabled entry reconfigured before its first setup under this version
    would lose that equality and keep the old address as its title."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=HOST,
        data=USER_INPUT,
        unique_id=SERIAL,
        disabled_by=ConfigEntryDisabler.USER,
    )
    entry.add_to_hass(hass)

    result = await _reconfigure(hass, entry)

    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "WAGO 3456"


MODULE = FoundModule(serial_number="033000000001", host=HOST)
OLD_HOST = "192.0.2.99"


async def test_the_user_step_offers_the_modules_the_search_found(hass, module_search):
    module_search.return_value = [MODULE]

    started = await _start(hass)
    fields = {str(key): value for key, value in started["data_schema"].schema.items()}

    host = fields[CONF_HOST]
    assert isinstance(host, SelectSelector)
    assert [option["value"] for option in host.config["options"]] == [HOST]
    # A module in another subnet is never found, so an address can still be typed.
    assert host.config["custom_value"] is True
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_user_step_leaves_out_a_module_already_configured(
    hass, module_search
):
    MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL).add_to_hass(hass)
    module_search.return_value = [MODULE]

    started = await _start(hass)
    fields = {str(key): value for key, value in started["data_schema"].schema.items()}

    assert isinstance(fields[CONF_HOST], TextSelector)


async def _discover(hass, module=MODULE):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data={"serial_number": module.serial_number, "host": module.host},
    )


async def test_a_found_module_is_offered_as_a_new_meter(hass):
    result = await _discover(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WAGO 3456"
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 502, CONF_UNIT_ID: 1}
    assert result["options"] == {
        CONF_MEASUREMENT_INTERVAL: DEFAULT_MEASUREMENT_INTERVAL,
        CONF_ENERGY_INTERVAL: DEFAULT_ENERGY_INTERVAL,
    }
    assert result["result"].unique_id == SERIAL


async def test_a_found_module_whose_meter_does_not_answer_is_not_offered(hass, meter):
    meter.fail_requests(ModbusConnectionError("down"))

    result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


def _own_module(hass, entry, serial=MODULE.serial_number):
    """The module device setup registers for an entry that reached it."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, serial)},
        model=MODULE_MODEL,
    )


def _module_serial_words(serial):
    """0x0408-0x040A as the module holds a serial: its digits as hex words."""
    return {
        0x0408 + index: int(serial[4 * index : 4 * index + 4], 16) for index in range(3)
    }


async def test_a_module_that_moved_takes_its_entry_along(hass, meter):
    """The module was given a new address; the entry follows and loads there,
    even with a unit id the search could not have guessed."""
    meter.load_module_raw(module_holding())
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, CONF_HOST: OLD_HOST, CONF_UNIT_ID: 1},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)
    _own_module(hass, entry)

    result = await _discover(hass)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert entry.state is ConfigEntryState.LOADED


async def test_a_module_reached_by_host_name_keeps_the_name(hass, meter):
    """A host name follows the module through DHCP on its own; replacing it
    with today's address would break exactly that."""
    # The module answers at the new address: only the name may stop the move.
    meter.load_module_raw(module_holding())
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, CONF_HOST: "wago-module"},
        unique_id=SERIAL,
    )
    entry.add_to_hass(hass)
    _own_module(hass, entry)

    result = await _discover(hass)

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "wago-module"


async def test_a_meter_known_without_its_module_gets_the_new_address(hass, meter):
    """An entry whose setup never reached the module has no module device;
    the meter's own serial still ties the found module to it."""
    meter.load_module_raw(module_holding())
    entry = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, CONF_HOST: OLD_HOST}, unique_id=SERIAL
    )
    entry.add_to_hass(hass)

    result = await _discover(hass)
    await hass.async_block_till_done()

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert entry.state is ConfigEntryState.LOADED


async def test_a_loaded_meter_known_without_its_module_reloads_once(
    hass, meter, caplog
):
    """Found by the meter's serial rather than the module device, a loaded
    entry moves the way reconfigure moves it: one reload, and none of the
    double reload core reports and drops in 2026.12."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, CONF_HOST: OLD_HOST}, unique_id=SERIAL
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # After setup, so the entry has no module device to be found by.
    meter.load_module_raw(module_holding())
    connections_before = len(meter.params_seen)

    result = await _discover(hass)
    await hass.async_block_till_done()

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert entry.state is ConfigEntryState.LOADED
    # The meter probe, the module check at the new address, one reload.
    assert len(meter.params_seen) == connections_before + 3
    assert "should use it for scheduling a reload" not in caplog.text


async def test_a_meter_known_without_its_module_keeps_its_host_name(hass, meter):
    meter.load_module_raw(module_holding())
    entry = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, CONF_HOST: "wago-module"}, unique_id=SERIAL
    )
    entry.add_to_hass(hass)

    result = await _discover(hass)

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "wago-module"


OTHER_MODULE = "033000000002"


async def test_a_new_module_on_an_entry_s_old_address_is_offered(hass, meter):
    """DHCP handed a new module the address an entry still holds; the entry
    has a module of its own, so the address alone says nothing."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    _own_module(hass, entry, OTHER_MODULE)
    meter.load_raw({"holding": {0x4000: 0x0099, 0x4001: 0x8765}})

    result = await _discover(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert entry.data[CONF_HOST] == HOST


async def test_two_modules_that_swapped_addresses_each_keep_their_entry(hass, meter):
    meter.load_module_raw(module_holding())
    # Added first: the entry now at the address is the one looked at first.
    other = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="00998765")
    other.add_to_hass(hass)
    _own_module(hass, other, OTHER_MODULE)
    moved = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, CONF_HOST: OLD_HOST}, unique_id=SERIAL
    )
    moved.add_to_hass(hass)
    _own_module(hass, moved)

    result = await _discover(hass)
    await hass.async_block_till_done()

    assert result["reason"] == "already_configured"
    assert moved.data[CONF_HOST] == HOST
    assert other.data[CONF_HOST] == HOST


@pytest.mark.parametrize(
    "module_raw",
    [
        # Another module answers there now: the reply was stale.
        {
            "holding": {
                **module_holding()["holding"],
                **_module_serial_words(OTHER_MODULE),
            }
        },
        # Nothing module-like answers there: the reply was not the module's.
        {"holding": {0x0400: 0}},
    ],
    ids=["another module", "no module"],
)
async def test_an_address_the_module_does_not_confirm_moves_nothing(
    hass, meter, module_raw
):
    """A search reply is a UDP datagram: stale by the time it is read, or not
    from the module at all. Before an entry moves, the module has to answer
    at the new address with its own serial."""
    meter.load_module_raw(module_raw)
    entry = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, CONF_HOST: OLD_HOST}, unique_id=SERIAL
    )
    entry.add_to_hass(hass)
    _own_module(hass, entry)

    result = await _discover(hass)

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == OLD_HOST


async def test_an_ignored_meter_is_probed_once_per_run(hass, meter):
    """The module takes four connections; a meter the user asked to leave
    alone may not cost one every quarter hour."""
    MockConfigEntry(
        domain=DOMAIN, source="ignore", data={}, unique_id=SERIAL
    ).add_to_hass(hass)

    first = await _discover(hass)
    connections = len(meter.params_seen)
    second = await _discover(hass)

    assert first["reason"] == second["reason"] == "already_configured"
    assert len(meter.params_seen) == connections


async def test_a_meter_waiting_under_discovered_can_be_added_by_hand(
    hass, module_search
):
    module_search.return_value = [MODULE]
    waiting = await _discover(hass)
    assert waiting["step_id"] == "discovery_confirm"

    started = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


MODULE_FORM = {
    "dhcp": False,
    "ip_address": HOST,
    "netmask": "255.255.255.0",
    "gateway": "192.0.2.1",
    "dns_server_1": "192.0.2.1",
    "dns_server_2": "192.0.2.1",
    "ntp": True,
    "ntp_server_1": "192.0.2.1",
    "ntp_server_2": "",
    "hostname": "Wago-TCP",
    "timeout": 3000,
}


async def _loaded_with_module(hass, meter, words=None, host=HOST):
    """An entry whose module lives at the entry's own address."""
    raw = module_holding()
    # The module's address as the entry reaches it: 0x0064-0x0065 = HOST.
    raw["holding"].update({0x0064: 0xC000, 0x0065: 0x020A, **(words or {})})
    meter.load_module_raw(raw)
    entry = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, CONF_HOST: host}, unique_id=SERIAL
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _module_form(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["intervals", "module"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "module"}
    )


def _module_writes(meter):
    writes = []
    meter.connections[-1].for_unit(MODULE_UNIT_ID).on_write(writes.append)
    return writes


async def test_the_module_page_shows_what_the_module_holds_now(hass, meter):
    entry = await _loaded_with_module(hass, meter)

    result = await _module_form(hass, entry)

    assert result["step_id"] == "module"
    defaults = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
    }
    assert defaults == MODULE_FORM


async def test_a_changed_setting_is_written_and_the_entry_reloaded(hass, meter):
    entry = await _loaded_with_module(hass, meter)
    result = await _module_form(hass, entry)
    writes = _module_writes(meter)
    connections_before = len(meter.params_seen)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**MODULE_FORM, "ntp_server_2": "192.0.2.1"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [write.address for write in writes] == [0x0000, 0x0064, 0x03F2, 0x03F1]
    # NTP server 2 at 0x0071-0x0072 of the network block.
    assert writes[1].values[0x0071 - 0x0064 : 0x0073 - 0x0064] == [0xC000, 0x0201]
    # The device page shows what was read at setup; one reload reads it anew.
    assert len(meter.params_seen) == connections_before + 1
    assert entry.state is ConfigEntryState.LOADED
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{MODULE.serial_number}_ntp_server_2"
    )
    assert hass.states.get(entity_id).state == "192.0.2.1"


async def test_an_unchanged_page_writes_nothing(hass, meter):
    """Every write goes to the module's flash; nothing changed, nothing to wear."""
    entry = await _loaded_with_module(hass, meter)
    result = await _module_form(hass, entry)
    writes = _module_writes(meter)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], MODULE_FORM
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert writes == []


async def test_a_new_module_address_takes_the_entry_along(hass, meter):
    entry = await _loaded_with_module(hass, meter)
    result = await _module_form(hass, entry)

    await hass.config_entries.options.async_configure(
        result["flow_id"], {**MODULE_FORM, "ip_address": "192.0.2.11"}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_HOST] == "192.0.2.11"
    assert entry.state is ConfigEntryState.LOADED
    assert meter.params_seen[-1].host == "192.0.2.11"


async def test_a_setting_the_module_cannot_use_is_a_form_error(hass, meter):
    entry = await _loaded_with_module(hass, meter)
    result = await _module_form(hass, entry)
    writes = _module_writes(meter)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**MODULE_FORM, "gateway": "198.51.100.1"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gateway_outside_network"}
    assert writes == []


async def test_a_write_the_module_refuses_is_a_form_error(hass, meter):
    entry = await _loaded_with_module(hass, meter)
    result = await _module_form(hass, entry)
    meter.connections[-1].for_unit(MODULE_UNIT_ID).fail_write(
        0x0064, ModbusConnectionError("down")
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**MODULE_FORM, "hostname": "meter-room"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "module_write_failed"}


async def test_a_module_gone_silent_ends_the_page(hass, meter):
    """The page shows what the module holds now, not what setup read; with
    no answer there is nothing true to show."""
    entry = await _loaded_with_module(hass, meter)
    meter.connections[-1].for_unit(MODULE_UNIT_ID).fail_requests(
        ModbusConnectionError("down")
    )

    result = await _module_form(hass, entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "module_not_answering"


async def test_without_the_module_the_options_are_the_intervals(hass, meter):
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=SERIAL)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "intervals"


NEW_ADDRESS = "192.0.2.11"
# The module's fixed address in its register while DHCP leased it HOST.
FIXED_ADDRESS = {0x0064: 0xC000, 0x0065: 0x0204}
DHCP_ON = {0x006A: 1}


async def _save_module(hass, entry, changes):
    result = await _module_form(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**result_defaults(result), **changes}
    )
    await hass.async_block_till_done()
    return result


def result_defaults(result):
    """The module page as it opened, unchanged."""
    return {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
    }


async def test_an_unconfirmed_apply_still_takes_the_entry_along(hass, meter):
    """Once stored, the module uses the address after its next restart at the
    latest; the entry has to be there, and the user has to know."""
    entry = await _loaded_with_module(hass, meter)
    meter.connections[-1].for_unit(MODULE_UNIT_ID).fail_write(
        0x03F1, ModbusConnectionError("gone")
    )

    result = await _save_module(hass, entry, {"ip_address": NEW_ADDRESS})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "module_not_applied"
    assert entry.data[CONF_HOST] == NEW_ADDRESS


async def test_switching_dhcp_off_takes_the_entry_to_the_fixed_address(hass, meter):
    """With DHCP on the entry reaches the lease, not the fixed address the
    module falls back to - which the user did not have to touch."""
    entry = await _loaded_with_module(hass, meter, {**FIXED_ADDRESS, **DHCP_ON})

    await _save_module(hass, entry, {"dhcp": False})

    assert entry.data[CONF_HOST] == "192.0.2.4"
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("words", "host"),
    [
        # DHCP stays on: the new fixed address is not where the module goes.
        (DHCP_ON, HOST),
        # The entry reaches the module through another address, a forwarded
        # port for one; the module's own address says nothing about it.
        (FIXED_ADDRESS, HOST),
    ],
    ids=["dhcp on", "reached through another address"],
)
async def test_a_new_fixed_address_that_is_not_the_entry_s_moves_nothing(
    hass, meter, words, host
):
    entry = await _loaded_with_module(hass, meter, words, host)

    await _save_module(hass, entry, {"ip_address": NEW_ADDRESS})

    assert entry.data[CONF_HOST] == host
    assert entry.state is ConfigEntryState.LOADED


async def test_switching_dhcp_off_keeps_an_entry_s_host_name(hass, meter):
    """A host name follows the module by itself; today's address would not."""
    entry = await _loaded_with_module(
        hass, meter, {**FIXED_ADDRESS, **DHCP_ON}, "wago-module"
    )

    await _save_module(hass, entry, {"dhcp": False})

    assert entry.data[CONF_HOST] == "wago-module"
    assert entry.state is ConfigEntryState.LOADED


async def test_a_found_module_whose_meter_is_no_wago_879_is_not_offered(hass, meter):
    meter.load_raw({"holding": {0x4002: 0x9999}})

    result = await _discover(hass)

    assert result["reason"] == "unsupported_meter"


async def test_the_search_goes_to_every_broadcast_address_of_home_assistant(
    hass, module_search
):
    """Which adapters count is the user's network setting in Home Assistant;
    with only the default one enabled that is the limited broadcast alone."""
    await _start(hass)

    (targets,) = module_search.await_args.args
    expected = await network.async_get_ipv4_broadcast_addresses(hass)
    assert set(targets) == {str(address) for address in expected}
    assert "255.255.255.255" in targets


async def test_a_search_that_cannot_open_a_socket_leaves_the_host_to_type(
    hass, module_search
):
    module_search.side_effect = OSError("no network")

    started = await _start(hass)
    fields = {str(key): value for key, value in started["data_schema"].schema.items()}

    assert isinstance(fields[CONF_HOST], TextSelector)
