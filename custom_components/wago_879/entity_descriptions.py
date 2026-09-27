"""What each register becomes in Home Assistant - the one place that decides.

Enabled by default: the thirteen values the replaced YAML block served plus
the per-phase and total power/energy figures a dashboard shows. Tariff (T1-T4)
and quadrant (Q1-Q4) counters are registered but disabled; a direct-connected
meter without tariff switching holds zero in most of them. Identity fields are
diagnostic. Units stay what the YAML declared (kW, kWh) so the statistics
already recorded keep their scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactiveEnergy,
    UnitOfReactivePower,
)

from .wago_879_api.registers import ENERGY_FIELDS, IDENTITY_FIELDS, MEASUREMENT_FIELDS


class Block(StrEnum):
    """Which coordinator feeds the entity."""

    MEASUREMENTS = "measurements"
    ENERGY = "energy"
    IDENTITY = "identity"


@dataclass(frozen=True, kw_only=True)
class WagoSensorDescription(SensorEntityDescription):
    """A sensor description that knows which block feeds it."""

    block: Block
    # The field whose value, read at setup, decides whether this sensor exists
    # at all: zero there means the meter leaves the register empty. Only for
    # fields that cannot be zero on a meter that fills them.
    populated_when_nonzero: str | None = None


LEGACY_UNIQUE_IDS: dict[str, str] = {
    "WagoSpannungL1": "voltage_l1",
    "WagoSpannungL2": "voltage_l2",
    "WagoSpannungL3": "voltage_l3",
    "WagoStromL1": "current_l1",
    "WagoStromL2": "current_l2",
    "WagoStromL3": "current_l3",
    "WagoWirkleistungGesamt": "active_power_total",
    "WagoWirkleistungL1": "active_power_l1",
    "WagoWirkleistungL2": "active_power_l2",
    "WagoWirkleistungL3": "active_power_l3",
    "WagoWirkenergieTotal": "active_energy_total",
    "WagoWirkenergieBezug": "active_energy_import",
    "WagoWirkenergieLieferung": "active_energy_export",
}

# Identity words that are device information, not a sensor. Only identity
# fields are filtered by this set (see SENSOR_DESCRIPTIONS below) - a
# measurement or energy field needs a deliberate decision, not a name added
# here.
OMITTED_FIELDS: frozenset[str] = frozenset(
    {
        "serial_number",
        "meter_code",
        "protocol_version",
        "software_version",
        "hardware_version",
    }
)

TARIFFS = ("_t1", "_t2", "_t3", "_t4")
QUADRANTS = ("q1", "q2", "q3", "q4")

_MEASUREMENT_KINDS: dict[str, tuple[SensorDeviceClass | None, str | None, int]] = {
    "voltage": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, 1),
    "current": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE, 1),
    "frequency": (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ, 2),
    "active_power": (SensorDeviceClass.POWER, UnitOfPower.KILO_WATT, 3),
    "reactive_power": (
        SensorDeviceClass.REACTIVE_POWER,
        UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        3,
    ),
    "apparent_power": (
        SensorDeviceClass.APPARENT_POWER,
        UnitOfApparentPower.KILO_VOLT_AMPERE,
        3,
    ),
    "power_factor": (SensorDeviceClass.POWER_FACTOR, None, 2),
}


def _measurement(field: str) -> WagoSensorDescription:
    kind = next(k for k in _MEASUREMENT_KINDS if field.startswith(k))
    device_class, unit, precision = _MEASUREMENT_KINDS[kind]
    return WagoSensorDescription(
        key=field,
        translation_key=field,
        block=Block.MEASUREMENTS,
        populated_when_nonzero=_POPULATED_WHEN_NONZERO.get(field),
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=precision,
    )


def _is_split_counter(field: str) -> bool:
    return field.endswith(TARIFFS) or any(f"_{q}" in field for q in QUADRANTS)


def _is_directed(field: str) -> bool:
    """Whether the register counts in one direction only.

    Import, export and the four quadrants each accumulate their own share and
    only ever rise. Every other energy register holds import minus export -
    measured on a meter: reactive_energy_total -1206.637 against import 652.664
    and export 1859.300 - so it falls, goes negative, and is not a rising
    counter however much its name says "total".
    """
    return (
        "import" in field
        or "export" in field
        or any(f"_{q}" in field for q in QUADRANTS)
    )


def _energy(field: str) -> WagoSensorDescription:
    if field == "tariff":
        return WagoSensorDescription(
            key=field,
            translation_key=field,
            block=Block.ENERGY,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
    reactive = field.startswith("reactive")
    enabled = not _is_split_counter(field) and not field.startswith("day_energy")
    return WagoSensorDescription(
        key=field,
        translation_key=field,
        block=Block.ENERGY,
        device_class=SensorDeviceClass.REACTIVE_ENERGY
        if reactive
        else SensorDeviceClass.ENERGY,
        native_unit_of_measurement=(
            UnitOfReactiveEnergy.KILO_VOLT_AMPERE_REACTIVE_HOUR
            if reactive
            else UnitOfEnergy.KILO_WATT_HOUR
        ),
        state_class=SensorStateClass.TOTAL_INCREASING
        if _is_directed(field)
        else SensorStateClass.TOTAL,
        suggested_display_precision=3,
        entity_registry_enabled_default=enabled,
    )


# The manual shades these rows grey without saying why, and a direct-measuring
# meter holds 0 in them. A running meter is powered by the mains it measures,
# so a filled voltage average is never 0; a current average of 0 A is a real
# reading at no load, so it follows the voltage average, which carries the same
# asterisk. No CT is ever rated 0 A, on either side.
_POPULATED_WHEN_NONZERO = {
    "voltage_avg": "voltage_avg",
    "current_avg": "voltage_avg",
    "ct_ratio_primary": "ct_ratio_primary",
    "ct_ratio_secondary": "ct_ratio_primary",
}


def _identity(field: str) -> WagoSensorDescription:
    return WagoSensorDescription(
        key=field,
        translation_key=field,
        block=Block.IDENTITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        populated_when_nonzero=_POPULATED_WHEN_NONZERO.get(field),
        entity_registry_enabled_default=field
        in {
            "ct_ratio_primary",
            "ct_ratio_secondary",
            "modbus_id",
            "current_quadrant",
        },
    )


SENSOR_DESCRIPTIONS: tuple[WagoSensorDescription, ...] = (
    *(_measurement(f) for f in MEASUREMENT_FIELDS),
    *(_energy(f) for f in ENERGY_FIELDS),
    *(_identity(f) for f in IDENTITY_FIELDS if f not in OMITTED_FIELDS),
)
