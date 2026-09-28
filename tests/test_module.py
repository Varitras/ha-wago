"""The 879-9000 module's own registers, read on its own unit.

The words are the ones the vendor's configuration tool read from a live
module (Modbus TCP, unit 255), with the addresses moved to the documentation
range and the serial number made up.
"""

import asyncio

from modbus_connection import ModbusConnectionError
from modbus_connection.mock import MockModbusConnection
import pytest

from custom_components.wago_879.wago_879_api.device import ModuleNotApplied, WagoModule
from custom_components.wago_879.wago_879_api.registers import MODULE_UNIT_ID


def _ip(text: str) -> list[int]:
    parts = [int(part) for part in text.split(".")]
    return [parts[0] << 8 | parts[1], parts[2] << 8 | parts[3]]


def _text(value: str, words: int) -> list[int]:
    raw = value.encode().ljust(words * 2, b"\0")
    return [raw[index] << 8 | raw[index + 1] for index in range(0, len(raw), 2)]


SETTINGS = [10, 1, 1, 3000, 1]
NETWORK = [
    *_ip("192.0.2.4"),
    *_ip("255.255.255.0"),
    *_ip("192.0.2.1"),
    0,
    *_ip("192.0.2.1"),
    *_ip("192.0.2.1"),
    *_ip("192.0.2.1"),
    *_ip("0.0.0.0"),
    *_text("Wago-TCP", 16),
    1,
]
VERSION = [330, 1, 10, 1, 0, 856, 856, 3, 0x0330, 0x0000, 0x0001, 1, 0]


def _words(start: int, values: list[int]) -> dict[int, int]:
    return {start + offset: value for offset, value in enumerate(values)}


def module_holding() -> dict[str, dict[int, int]]:
    """The module's three register tables, as a mock unit loads them."""
    return {
        "holding": {
            **_words(0x0000, SETTINGS),
            **_words(0x0064, NETWORK),
            **_words(0x0400, VERSION),
        }
    }


@pytest.fixture
def unit():
    module = MockModbusConnection().for_unit(MODULE_UNIT_ID)
    module.load_raw(module_holding())
    return module


async def test_the_module_reports_what_its_configuration_tool_shows(unit):
    """Every field the tool displays, against the tool's own screen."""
    values = await WagoModule(unit).async_read()

    assert values == {
        "serial_number": "033000000001",
        "firmware_version": "1.0.856",
        "bootloader_version": "1.0.856",
        "dhcp": False,
        "ip_address": "192.0.2.4",
        "netmask": "255.255.255.0",
        "gateway": "192.0.2.1",
        "dns_server_1": "192.0.2.1",
        "dns_server_2": "192.0.2.1",
        "ntp": True,
        "ntp_server_1": "192.0.2.1",
        "ntp_server_2": "0.0.0.0",
        "hostname": "Wago-TCP",
        "modbus_port": "rs232",
        "baud_rate": 115200,
        "parity": "even",
        "timeout": 3000,
    }


async def test_a_code_nobody_has_seen_stays_unknown(unit):
    """Only codes checked against the tool are named; a guess would be shown
    as a fact on the device page."""
    unit.load_raw({"holding": {0x0001: 7, 0x0002: 9}})

    values = await WagoModule(unit).async_read()

    assert values["parity"] is None
    assert values["modbus_port"] is None


async def test_a_module_that_does_not_answer_raises(unit):
    """Whether a missing module is fatal is the caller's decision."""
    unit.fail_requests(ModbusConnectionError("down"))

    with pytest.raises(ModbusConnectionError):
        await WagoModule(unit).async_read()


async def test_another_gateway_on_unit_255_is_not_taken_for_the_module(unit):
    """Unit 255 of another gateway may answer too; the 879-9000 reports
    device type 330, its maker's article number, in its version block."""
    unit.load_raw({"holding": {0x0400: 0}})

    assert await WagoModule(unit).async_read() is None


WRITE_SINGLE_REGISTER = 0x06
WRITE_MULTIPLE_REGISTERS = 0x10


async def test_a_change_is_written_stored_and_applied_as_the_tool_does(unit):
    """The order and shape the vendor tool used in the capture: both blocks
    whole, then "store", then "apply"."""
    writes = []
    unit.on_write(writes.append)

    await WagoModule(unit).async_write(
        {"ntp_server_2": "192.0.2.1", "hostname": "meter-room", "timeout": 2000}
    )

    assert [(write.address, write.function_code) for write in writes] == [
        (0x0000, WRITE_MULTIPLE_REGISTERS),
        (0x0064, WRITE_MULTIPLE_REGISTERS),
        (0x03F2, WRITE_SINGLE_REGISTER),
        (0x03F1, WRITE_SINGLE_REGISTER),
    ]
    # Word 4 means nothing the tool shows; it goes back as it was read.
    assert writes[0].values == [10, 1, 1, 2000, 1]
    assert writes[2].values == writes[3].values == [1]
    values = await WagoModule(unit).async_read()
    assert values["ntp_server_2"] == "192.0.2.1"
    assert values["hostname"] == "meter-room"
    assert values["timeout"] == 2000
    assert values["ip_address"] == "192.0.2.4"
    assert values["ntp"] is True


async def test_a_switch_is_written_as_a_word(unit):
    await WagoModule(unit).async_write({"dhcp": True, "ntp": False})

    values = await WagoModule(unit).async_read()
    assert values["dhcp"] is True
    assert values["ntp"] is False


async def test_a_failed_write_is_not_stored(unit):
    """A block the module refused must not be followed by "store": that would
    keep the other block's half of a change."""
    writes = []
    unit.on_write(writes.append)
    unit.fail_write(0x0064, ModbusConnectionError("down"))

    with pytest.raises(ModbusConnectionError):
        await WagoModule(unit).async_write({"hostname": "meter-room"})

    assert 0x03F2 not in [write.address for write in writes]


async def test_a_timeout_above_32767_reads_back_as_written(unit):
    """The module's words are unsigned: 40000 ms is a timeout, not -25536."""
    await WagoModule(unit).async_write({"timeout": 40000})

    assert (await WagoModule(unit).async_read())["timeout"] == 40000


async def test_version_words_are_unsigned(unit):
    unit.load_raw({"holding": {0x0405: 40000}})

    assert (await WagoModule(unit).async_read())["firmware_version"] == "1.0.40000"


async def test_a_store_the_module_refuses_is_a_plain_failure(unit):
    unit.fail_write(0x03F2, ModbusConnectionError("down"))

    with pytest.raises(ModbusConnectionError):
        await WagoModule(unit).async_write({"hostname": "meter-room"})


async def test_an_unconfirmed_apply_says_the_settings_are_stored(unit):
    """After "store" the module keeps the settings whatever "apply" answers;
    the caller has to know the difference."""
    unit.fail_write(0x03F1, ModbusConnectionError("down"))

    with pytest.raises(ModuleNotApplied):
        await WagoModule(unit).async_write({"hostname": "meter-room"})


async def test_two_writes_at_once_keep_both_changes(unit):
    """Each write sends whole blocks read just before; interleaved, the later
    one would put back what the earlier one changed."""
    read = unit.read_holding_registers

    async def read_and_yield(address, count):
        words = await read(address, count)
        await asyncio.sleep(0)
        return words

    unit.read_holding_registers = read_and_yield
    module = WagoModule(unit)

    await asyncio.gather(
        module.async_write({"timeout": 2500}),
        module.async_write({"hostname": "meter-room"}),
    )

    unit.read_holding_registers = read
    values = await module.async_read()
    assert values["timeout"] == 2500
    assert values["hostname"] == "meter-room"
