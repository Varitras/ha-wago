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


def _values(component: Component, names: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        value = getattr(component, name)
        # The meter reports an unavailable measurement as NaN; None is what an
        # entity can show as unknown, NaN is not.
        result[name] = None if isinstance(value, float) and math.isnan(value) else value
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
        """Read the 0x4000 block; raises ModbusError on a link problem."""
        await self._identity.async_update()
        values = _values(self._identity, IDENTITY_FIELDS)
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
