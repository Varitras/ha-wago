"""The 879-3000 register map as modbus-connection components.

Source: WAGO manual 4PU/4PS/2PU CT (EN V1.6), appendix A3.2. Every measured
value is "Float ABCD" - big-endian word order, which is the library default -
over two holding registers, function code 03. The one exception in the
counter block is the tariff word at 0x6048.

Addresses live here and nowhere else.
"""

from modbus_connection.model import Component
from modbus_connection.model.fields import FloatField, NumberField, RawField

FLOAT_WORDS = 2


def _float(address: int) -> FloatField:
    return FloatField(address, count=FLOAT_WORDS)


def _number(address: int, *, count: int = 1) -> NumberField[int]:
    # NumberField is generic; without the explicit NumberField[int] return
    # type here, mypy cannot infer T from a bare NumberField(...) call and
    # reports "Need type annotation" at every use site instead of once here.
    return NumberField(address, count=count)


class Identity(Component):
    """Serial number, versions and settings at 0x4000; read once at setup."""

    register_space = "holding"

    serial_number = RawField(0x4000, count=2)
    meter_code = RawField(0x4002)
    modbus_id = _number(0x4003)
    protocol_version = _float(0x4005)
    software_version = _float(0x4007)
    hardware_version = _float(0x4009)
    meter_amperes = _number(0x400B)
    power_down_counter = _number(0x4016)
    current_quadrant = _number(0x4017)
    quadrant_l1 = _number(0x4018)
    quadrant_l2 = _number(0x4019)
    quadrant_l3 = _number(0x401A)
    # The manual lists "CT ratio" at 0x401F as one two-register field, but its
    # own write command (page 38) sends two words for it:
    #   01 10 401F 0002 04 9995 0005   "Set to 9995/5"
    # - primary current in the first word, secondary current in the second. A
    # single 32-bit value would decode that example to nonsense, so the two
    # words are separate fields here.
    # Left undecided: the frame is printed in hex, yet "9995 0005" reads as
    # decimal digits (0x9995 would be 39317). Nothing establishes which, and
    # the meter this was verified against is a direct-measuring 4PU that holds
    # 0 in both words. Both words are therefore reported as the meter sends
    # them, with no scaling applied.
    ct_ratio_primary = _number(0x401F)
    ct_ratio_secondary = _number(0x4020)


class Measurements(Component):
    """Instantaneous values 0x5000..0x5037: one contiguous block read."""

    register_space = "holding"

    voltage_avg = _float(0x5000)
    voltage_l1 = _float(0x5002)
    voltage_l2 = _float(0x5004)
    voltage_l3 = _float(0x5006)
    frequency = _float(0x5008)
    current_avg = _float(0x500A)
    current_l1 = _float(0x500C)
    current_l2 = _float(0x500E)
    current_l3 = _float(0x5010)
    active_power_total = _float(0x5012)
    active_power_l1 = _float(0x5014)
    active_power_l2 = _float(0x5016)
    active_power_l3 = _float(0x5018)
    reactive_power_total = _float(0x501A)
    reactive_power_l1 = _float(0x501C)
    reactive_power_l2 = _float(0x501E)
    reactive_power_l3 = _float(0x5020)
    apparent_power_total = _float(0x5022)
    apparent_power_l1 = _float(0x5024)
    apparent_power_l2 = _float(0x5026)
    apparent_power_l3 = _float(0x5028)
    power_factor_total = _float(0x502A)
    power_factor_l1 = _float(0x502C)
    power_factor_l2 = _float(0x502E)
    power_factor_l3 = _float(0x5030)
    voltage_l1_l2 = _float(0x5032)
    voltage_l1_l3 = _float(0x5034)
    voltage_l2_l3 = _float(0x5036)


class Energy(Component):
    """Energy counters 0x6000..0x6090; the library splits this into two reads."""

    register_space = "holding"

    active_energy_total = _float(0x6000)
    active_energy_total_t1 = _float(0x6002)
    active_energy_total_t2 = _float(0x6004)
    active_energy_total_l1 = _float(0x6006)
    active_energy_total_l2 = _float(0x6008)
    active_energy_total_l3 = _float(0x600A)
    active_energy_import = _float(0x600C)
    active_energy_import_t1 = _float(0x600E)
    active_energy_import_t2 = _float(0x6010)
    active_energy_import_l1 = _float(0x6012)
    active_energy_import_l2 = _float(0x6014)
    active_energy_import_l3 = _float(0x6016)
    active_energy_export = _float(0x6018)
    active_energy_export_t1 = _float(0x601A)
    active_energy_export_t2 = _float(0x601C)
    active_energy_export_l1 = _float(0x601E)
    active_energy_export_l2 = _float(0x6020)
    active_energy_export_l3 = _float(0x6022)
    reactive_energy_total = _float(0x6024)
    reactive_energy_total_t1 = _float(0x6026)
    reactive_energy_total_t2 = _float(0x6028)
    reactive_energy_total_l1 = _float(0x602A)
    reactive_energy_total_l2 = _float(0x602C)
    reactive_energy_total_l3 = _float(0x602E)
    reactive_energy_import = _float(0x6030)
    reactive_energy_import_t1 = _float(0x6032)
    reactive_energy_import_t2 = _float(0x6034)
    reactive_energy_import_l1 = _float(0x6036)
    reactive_energy_import_l2 = _float(0x6038)
    reactive_energy_import_l3 = _float(0x603A)
    reactive_energy_export = _float(0x603C)
    reactive_energy_export_t1 = _float(0x603E)
    reactive_energy_export_t2 = _float(0x6040)
    reactive_energy_export_l1 = _float(0x6042)
    reactive_energy_export_l2 = _float(0x6044)
    reactive_energy_export_l3 = _float(0x6046)
    tariff = _number(0x6048)
    day_energy = _float(0x6049)
    active_energy_total_t3 = _float(0x604B)
    active_energy_total_t4 = _float(0x604D)
    active_energy_import_t3 = _float(0x604F)
    active_energy_import_t4 = _float(0x6051)
    active_energy_export_t3 = _float(0x6053)
    active_energy_export_t4 = _float(0x6055)
    reactive_energy_total_t3 = _float(0x6057)
    reactive_energy_total_t4 = _float(0x6059)
    reactive_energy_import_t3 = _float(0x605B)
    reactive_energy_import_t4 = _float(0x605D)
    reactive_energy_export_t3 = _float(0x605F)
    reactive_energy_export_t4 = _float(0x6061)
    reactive_energy_q1 = _float(0x6063)
    reactive_energy_q1_t1 = _float(0x6065)
    reactive_energy_q1_t2 = _float(0x6067)
    reactive_energy_q1_t3 = _float(0x6069)
    reactive_energy_q1_t4 = _float(0x606B)
    reactive_energy_q2 = _float(0x606D)
    reactive_energy_q2_t1 = _float(0x606F)
    reactive_energy_q2_t2 = _float(0x6071)
    reactive_energy_q2_t3 = _float(0x6073)
    reactive_energy_q2_t4 = _float(0x6075)
    reactive_energy_q3 = _float(0x6077)
    reactive_energy_q3_t1 = _float(0x6079)
    reactive_energy_q3_t2 = _float(0x607B)
    reactive_energy_q3_t3 = _float(0x607D)
    reactive_energy_q3_t4 = _float(0x607F)
    reactive_energy_q4 = _float(0x6081)
    reactive_energy_q4_t1 = _float(0x6083)
    reactive_energy_q4_t2 = _float(0x6085)
    reactive_energy_q4_t3 = _float(0x6087)
    reactive_energy_q4_t4 = _float(0x6089)
    day_energy_l1 = _float(0x608B)
    day_energy_l2 = _float(0x608D)
    day_energy_l3 = _float(0x608F)


def _field_names(component: type[Component]) -> tuple[str, ...]:
    fields = component.declared_fields
    return tuple(sorted(fields, key=lambda name: fields[name].address))


def address_of(component: type[Component], name: str) -> int:
    """The register address a component attribute reads from."""
    return int(component.declared_fields[name].address)


IDENTITY_FIELDS = _field_names(Identity)
MEASUREMENT_FIELDS = _field_names(Measurements)
ENERGY_FIELDS = _field_names(Energy)
