"""WagoMeter reads each block and hands back plain dicts; link errors propagate."""

import math
import struct

from modbus_connection import ModbusConnectionError
from modbus_connection.mock import MockModbusConnection
import pytest

from custom_components.wago_879.wago_879_api.device import WagoMeter
from custom_components.wago_879.wago_879_api.registers import (
    ENERGY_FIELDS,
    MEASUREMENT_FIELDS,
)


def _words(value: float) -> tuple[int, int]:
    raw = struct.pack(">f", value)
    return int.from_bytes(raw[:2], "big"), int.from_bytes(raw[2:], "big")


def _holding(values: dict[int, float | int]) -> dict[str, dict[int, int]]:
    holding: dict[int, int] = {}
    for address, value in values.items():
        if isinstance(value, float):
            high, low = _words(value)
            holding[address] = high
            holding[address + 1] = low
        else:
            holding[address] = value
    return {"holding": holding}


@pytest.fixture
def unit():
    connection = MockModbusConnection()
    return connection.for_unit(1)


async def test_measurements_come_back_keyed_by_field(unit):
    unit.load_raw(_holding({0x5002: 230.5, 0x5012: 1.25}))
    meter = WagoMeter(unit)

    values = await meter.async_update_measurements()

    assert set(values) == set(MEASUREMENT_FIELDS)
    assert values["voltage_l1"] == pytest.approx(230.5)
    assert values["active_power_total"] == pytest.approx(1.25)


async def test_energy_includes_the_tariff_word(unit):
    unit.load_raw(_holding({0x6000: 1234.5, 0x6048: 2}))
    meter = WagoMeter(unit)

    values = await meter.async_update_energy()

    assert set(values) == set(ENERGY_FIELDS)
    assert values["active_energy_total"] == pytest.approx(1234.5)
    assert values["tariff"] == 2


async def test_nan_from_the_meter_becomes_none(unit):
    unit.load_raw(_holding({0x5008: math.nan}))
    meter = WagoMeter(unit)

    values = await meter.async_update_measurements()

    assert values["frequency"] is None


async def test_identity_exposes_the_serial_as_hex(unit):
    unit.load_raw({"holding": {0x4000: 0x0012, 0x4001: 0x3456, 0x4003: 1}})
    meter = WagoMeter(unit)

    identity = await meter.async_read_identity()

    assert identity["modbus_id"] == 1
    assert meter.serial_number == "00123456"


async def test_serial_is_none_before_identity_was_read(unit):
    assert WagoMeter(unit).serial_number is None


async def test_a_link_error_propagates(unit):
    unit.fail_requests(ModbusConnectionError("down"))
    meter = WagoMeter(unit)

    with pytest.raises(ModbusConnectionError):
        await meter.async_update_measurements()
