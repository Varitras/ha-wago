"""The meter behind one Modbus unit: three blocks, three reads."""

from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Address
import math
from typing import Any

from modbus_connection import ModbusUnit
from modbus_connection.encode import encode_string
from modbus_connection.model import Component
from modbus_connection.model.fields import IPv4Field, RegisterField, StringField

from .registers import (
    ENERGY_FIELDS,
    IDENTITY_FIELDS,
    MEASUREMENT_FIELDS,
    MODULE_APPLY_COMMAND,
    MODULE_DEVICE_TYPE,
    MODULE_NETWORK_ADDRESS,
    MODULE_NETWORK_WORDS,
    MODULE_SETTINGS_ADDRESS,
    MODULE_SETTINGS_WORDS,
    MODULE_STORE_COMMAND,
    SERIAL_WORDS,
    Energy,
    Identity,
    Measurements,
    ModuleNetwork,
    ModuleSettings,
    ModuleVersion,
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


# The meter's own coding for its serial settings (its manual, registers 0x4004
# and 0x4011); the module's tool showed 115200 and EVEN for codes 10 and 1.
BAUD_RATES = {
    1: 300,
    2: 600,
    3: 1200,
    4: 2400,
    5: 4800,
    6: 9600,
    7: 19200,
    8: 38400,
    9: 57600,
    10: 115200,
}
PARITIES = {1: "even", 2: "none", 3: "odd"}
# Only the code the tool was seen to show; RS-485's code is unknown.
PORTS = {1: "rs232"}
HEX_DIGITS_PER_WORD = 4
WORD_RANGE = 0x10000
# What the tool writes to a command register to run the command.
COMMAND_RUN = 1

# Two groups of the keys async_read reports, for whoever treats them alike:
# on/off switches, and servers the module talks to (0.0.0.0 when not set).
MODULE_SWITCHES = ("dhcp", "ntp")
MODULE_SERVERS = ("dns_server_1", "dns_server_2", "ntp_server_1", "ntp_server_2")

# The settings a user may change, under the keys async_read reports them by.
# Port, baud rate and parity stay out: the module's serial side is fixed to
# the meter's 115200 baud, even parity.
WRITABLE_FIELDS: dict[str, RegisterField[Any]] = {
    "timeout": ModuleSettings.timeout,
    "ip_address": ModuleNetwork.ip_address,
    "netmask": ModuleNetwork.netmask,
    "gateway": ModuleNetwork.gateway,
    "dhcp": ModuleNetwork.dhcp,
    "dns_server_1": ModuleNetwork.dns_server_1,
    "dns_server_2": ModuleNetwork.dns_server_2,
    "ntp_server_1": ModuleNetwork.ntp_server_1,
    "ntp_server_2": ModuleNetwork.ntp_server_2,
    "hostname": ModuleNetwork.hostname,
    "ntp": ModuleNetwork.ntp,
}


def _encode(field: RegisterField[Any], value: Any) -> list[int]:
    if isinstance(field, IPv4Field):
        return list(divmod(int(IPv4Address(value)), WORD_RANGE))
    if isinstance(field, StringField):
        return encode_string(value, length=field.count)
    return [int(value)]


def _named[T](codes: dict[int, T], code: int | None) -> T | None:
    """The meaning of a code, or None for one that was not read or not seen."""
    if code is None:
        return None
    return codes.get(code)


class WagoModule:
    """Reads and writes the 879-9000 module itself, on its own Modbus unit."""

    def __init__(self, unit: ModbusUnit) -> None:
        """Bind the components to the module's unit; nothing is read yet."""
        self._unit = unit
        self._settings = ModuleSettings(unit)
        self._network = ModuleNetwork(unit)
        self._version = ModuleVersion(unit)

    async def async_read(self) -> dict[str, Any] | None:
        """Everything the configuration tool shows, None for another gateway.

        Raises ModbusError when unit 255 does not answer at all.
        """
        await self._version.async_update()
        if self._version.device_type != MODULE_DEVICE_TYPE:
            return None
        await self._settings.async_update()
        await self._network.async_update()
        settings, network, version = self._settings, self._network, self._version
        return {
            "serial_number": (
                f"{version.serial_number:0{SERIAL_WORDS * HEX_DIGITS_PER_WORD}X}"
            ),
            "firmware_version": (
                f"{version.firmware_major}.{version.firmware_minor}"
                f".{version.firmware_build}"
            ),
            "bootloader_version": (
                f"{version.bootloader_major}.{version.bootloader_minor}"
                f".{version.bootloader_build}"
            ),
            "dhcp": bool(network.dhcp),
            "ip_address": str(network.ip_address),
            "netmask": str(network.netmask),
            "gateway": str(network.gateway),
            "dns_server_1": str(network.dns_server_1),
            "dns_server_2": str(network.dns_server_2),
            "ntp": bool(network.ntp),
            "ntp_server_1": str(network.ntp_server_1),
            "ntp_server_2": str(network.ntp_server_2),
            "hostname": network.hostname,
            "modbus_port": _named(PORTS, settings.port_code),
            "baud_rate": _named(BAUD_RATES, settings.baud_rate_code),
            "parity": _named(PARITIES, settings.parity_code),
            "timeout": settings.timeout,
        }

    async def async_write(self, changes: Mapping[str, Any]) -> None:
        """Write `changes` the way the configuration tool does.

        Both blocks go whole, then "store", then "apply". The keys are those
        of WRITABLE_FIELDS; every word a change does not touch is read first
        and written back as it was. Raises ModbusError; a block the module
        refuses stops the write before anything is stored.
        """
        blocks = {
            MODULE_SETTINGS_ADDRESS: await self._unit.read_holding_registers(
                MODULE_SETTINGS_ADDRESS, MODULE_SETTINGS_WORDS
            ),
            MODULE_NETWORK_ADDRESS: await self._unit.read_holding_registers(
                MODULE_NETWORK_ADDRESS, MODULE_NETWORK_WORDS
            ),
        }
        for key, value in changes.items():
            field = WRITABLE_FIELDS[key]
            start = max(address for address in blocks if address <= field.address)
            offset = field.address - start
            blocks[start][offset : offset + field.count] = _encode(field, value)
        for start, words in blocks.items():
            await self._unit.write_registers(start, words)
        await self._unit.write_register(MODULE_STORE_COMMAND, COMMAND_RUN)
        await self._unit.write_register(MODULE_APPLY_COMMAND, COMMAND_RUN)
