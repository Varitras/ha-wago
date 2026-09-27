"""The meter behind one Modbus unit: three blocks, three reads."""

from __future__ import annotations

import math
from typing import Any

from modbus_connection import ModbusUnit
from modbus_connection.model import Component

from .registers import (
    ENERGY_FIELDS,
    IDENTITY_FIELDS,
    MEASUREMENT_FIELDS,
    Energy,
    Identity,
    Measurements,
)

SERIAL_HEX_DIGITS = 8

# The meter codes the manual (appendix A3.2, register 0x4002) lists for the
# variants sharing this register map: 4PU, 4PS and 2PU CT.
SUPPORTED_METER_CODES = frozenset({0x1111, 0x1112, 0x1113})


class UnsupportedMeter(Exception):
    """The device answers, but not with a meter code this register map is for."""

    def __init__(self, meter_code: int) -> None:
        """Keep the code the device reported."""
        super().__init__(f"meter code 0x{meter_code:04X}")
        self.meter_code = meter_code


def _values(component: Component, names: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        value = getattr(component, name)
        # The meter reports an unavailable measurement as NaN, and a float
        # register can hold an infinity; None is what an entity can show as
        # unknown, Home Assistant refuses a non-finite state outright.
        is_unusable = isinstance(value, float) and not math.isfinite(value)
        result[name] = None if is_unusable else value
    return result


class WagoMeter:
    """Reads the identity, measurement and energy blocks of one meter."""

    def __init__(self, unit: ModbusUnit) -> None:
        """Bind the components to the unit; nothing is read yet."""
        self._identity = Identity(unit)
        self._measurements = Measurements(unit)
        self._energy = Energy(unit)
        self._serial_number: str | None = None

    @property
    def serial_number(self) -> str | None:
        """The serial as printed on the meter, after the identity was read."""
        return self._serial_number

    async def async_read_identity(self) -> dict[str, Any]:
        """Read the 0x4000 block.

        Raises ModbusError on a link problem and UnsupportedMeter when the
        device is not one of the meters this register map describes.
        """
        await self._identity.async_update()
        values = _values(self._identity, IDENTITY_FIELDS)
        if values["meter_code"] not in SUPPORTED_METER_CODES:
            raise UnsupportedMeter(values["meter_code"])
        self._serial_number = f"{values['serial_number']:0{SERIAL_HEX_DIGITS}X}"
        return values

    async def async_update_measurements(self) -> dict[str, Any]:
        """Read the 0x5000 block; raises ModbusError on a link problem."""
        await self._measurements.async_update()
        return _values(self._measurements, MEASUREMENT_FIELDS)

    async def async_update_energy(self) -> dict[str, Any]:
        """Read the 0x6000 block; raises ModbusError on a link problem."""
        await self._energy.async_update()
        return _values(self._energy, ENERGY_FIELDS)
