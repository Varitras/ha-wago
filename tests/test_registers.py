"""The register layout matches the manual, and every value decodes.

The addresses come from the WAGO manual (4PU/4PS/2PU CT, EN V1.6, appendix
A3.2). The six the replaced YAML block used were cross-checked against a live
meter, which is what makes the manual trustworthy as the source here.
"""

import ast
import pathlib
import struct

import pytest

from custom_components.wago_879.wago_879_api import registers
from custom_components.wago_879.wago_879_api.registers import (
    ENERGY_FIELDS,
    IDENTITY_FIELDS,
    MEASUREMENT_FIELDS,
    Energy,
    Identity,
    Measurements,
    address_of,
)

# (component, attribute, address) - a sample across every block, the six the
# legacy YAML used first.
MANUAL_ROWS = [
    (Measurements, "voltage_l1", 0x5002),
    (Measurements, "current_l1", 0x500C),
    (Measurements, "active_power_total", 0x5012),
    (Energy, "active_energy_total", 0x6000),
    (Energy, "active_energy_import", 0x600C),
    (Energy, "active_energy_export", 0x6018),
    (Measurements, "voltage_avg", 0x5000),
    (Measurements, "frequency", 0x5008),
    (Measurements, "power_factor_l3", 0x5030),
    (Measurements, "voltage_l2_l3", 0x5036),
    (Energy, "reactive_energy_export_l3", 0x6046),
    (Energy, "active_energy_total_t4", 0x604D),
    (Energy, "reactive_energy_q4_t4", 0x6089),
    (Energy, "day_energy_l3", 0x608F),
    (Identity, "serial_number", 0x4000),
    (Identity, "meter_code", 0x4002),
    (Identity, "modbus_id", 0x4003),
    (Identity, "software_version", 0x4007),
    (Identity, "ct_ratio_primary", 0x401F),
    (Identity, "ct_ratio_secondary", 0x4020),
    (Identity, "current_quadrant", 0x4017),
    (Identity, "power_down_counter", 0x4016),
    (Energy, "tariff", 0x6048),
]


@pytest.mark.parametrize(("component", "name", "address"), MANUAL_ROWS)
def test_field_sits_at_the_manual_address(component, name, address):
    assert address_of(component, name) == address


def test_measurement_fields_are_contiguous_two_word_floats():
    """0x5000..0x5036 is 28 float32 values with no gap - one block read."""
    addresses = [address_of(Measurements, n) for n in MEASUREMENT_FIELDS]
    assert addresses == list(range(0x5000, 0x5038, 2))
    assert all(Measurements.declared_fields[n].count == 2 for n in MEASUREMENT_FIELDS)


def test_energy_fields_cover_the_counter_block():
    """0x6000..0x6047 float32 in steps of two, then the tariff word at 0x6048,
    then float32 from 0x6049 to 0x608F."""
    addresses = [address_of(Energy, n) for n in ENERGY_FIELDS]
    expected = [*range(0x6000, 0x6048, 2), 0x6048, *range(0x6049, 0x6091, 2)]
    assert addresses == expected


# Every Identity attribute's address, transcribed from the WAGO manual
# (4PU/4PS/2PU CT, EN V1.6, appendix A3.2).
IDENTITY_MANUAL_ADDRESSES = {
    "serial_number": 0x4000,
    "meter_code": 0x4002,
    "modbus_id": 0x4003,
    "protocol_version": 0x4005,
    "software_version": 0x4007,
    "hardware_version": 0x4009,
    "meter_amperes": 0x400B,
    "power_down_counter": 0x4016,
    "current_quadrant": 0x4017,
    "quadrant_l1": 0x4018,
    "quadrant_l2": 0x4019,
    "quadrant_l3": 0x401A,
    # Manual page 38 writes 0x401F as two words, primary then secondary.
    "ct_ratio_primary": 0x401F,
    "ct_ratio_secondary": 0x4020,
}


def test_every_identity_field_matches_its_manual_address():
    """Pins each Identity attribute to its manual address (appendix A3.2)."""
    assert set(IDENTITY_FIELDS) == set(IDENTITY_MANUAL_ADDRESSES)
    for name in IDENTITY_FIELDS:
        assert address_of(Identity, name) == IDENTITY_MANUAL_ADDRESSES[name]


def test_every_field_name_is_unique_across_components():
    names = [*MEASUREMENT_FIELDS, *ENERGY_FIELDS, *IDENTITY_FIELDS]
    assert len(names) == len(set(names))


def _float_words(value: float) -> list[int]:
    raw = struct.pack(">f", value)
    return [int.from_bytes(raw[:2], "big"), int.from_bytes(raw[2:], "big")]


def test_float_abcd_decodes_big_endian():
    """230.5 V as the manual's 'Float ABCD': high word first."""
    field = Measurements.declared_fields["voltage_l1"]
    assert field.decode(_float_words(230.5)) == pytest.approx(230.5)
    assert field.decode([0x4366, 0x8000]) == pytest.approx(230.5)


def test_ct_ratio_decodes_as_two_words_not_one_scalar():
    """The manual's own write command for 0x401F (page 38):

        01 10 401F 0002 04 9995 0005   "Set to 9995/5"

    Byte count 04, so one word per current. Whether those digits are hex or
    decimal is undecided (see registers.py); the split is what is asserted, so
    the words are taken at face value.
    """
    primary_word, secondary_word = 9995, 5
    primary = Identity.declared_fields["ct_ratio_primary"]
    secondary = Identity.declared_fields["ct_ratio_secondary"]

    assert primary.decode([primary_word]) == primary_word
    assert secondary.decode([secondary_word]) == secondary_word
    assert address_of(Identity, "ct_ratio_secondary") == (
        address_of(Identity, "ct_ratio_primary") + 1
    )
    assert primary.count == secondary.count == 1


def test_the_api_package_imports_no_home_assistant():
    """The layout and reads must stay testable on a machine without Home Assistant."""
    api = pathlib.Path(registers.__file__).parent
    offenders = []
    for source in api.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            offenders += [
                f"{source.name}: {n}" for n in names if n.startswith("homeassistant")
            ]
    assert not offenders
