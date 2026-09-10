"""Every register carries exactly one decision, and the legacy ids map 1:1.

The completeness register of this integration: the real surface is the set
of fields the components declare; the decision is a sensor description (with
its enabled/diagnostic choice) or a listed omission. A new register without a
decision is red.
"""

import pytest

pytest.importorskip("homeassistant")

from custom_components.wago_879.entity_descriptions import (
    LEGACY_UNIQUE_IDS,
    OMITTED_FIELDS,
    SENSOR_DESCRIPTIONS,
    Block,
    WagoSensorDescription,
)
from custom_components.wago_879.wago_879_api.registers import (
    ENERGY_FIELDS,
    IDENTITY_FIELDS,
    MEASUREMENT_FIELDS,
    Energy,
    Measurements,
    address_of,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory

ALL_FIELDS = {*MEASUREMENT_FIELDS, *ENERGY_FIELDS, *IDENTITY_FIELDS}
DESCRIBED = {d.key for d in SENSOR_DESCRIPTIONS}

# What the YAML block called each sensor, and which field it read.
LEGACY = {
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


# The register address the replaced YAML configuration read for each legacy
# unique_id, verified against the original YAML.
LEGACY_ADDRESSES = {
    "WagoSpannungL1": 0x5002,
    "WagoSpannungL2": 0x5004,
    "WagoSpannungL3": 0x5006,
    "WagoStromL1": 0x500C,
    "WagoStromL2": 0x500E,
    "WagoStromL3": 0x5010,
    "WagoWirkleistungGesamt": 0x5012,
    "WagoWirkleistungL1": 0x5014,
    "WagoWirkleistungL2": 0x5016,
    "WagoWirkleistungL3": 0x5018,
    "WagoWirkenergieTotal": 0x6000,
    "WagoWirkenergieBezug": 0x600C,
    "WagoWirkenergieLieferung": 0x6018,
}


def test_every_field_has_exactly_one_decision():
    undecided = ALL_FIELDS - DESCRIBED - OMITTED_FIELDS
    both = DESCRIBED & OMITTED_FIELDS
    assert not undecided, f"register(s) without a decision: {sorted(undecided)}"
    assert not both, f"described AND omitted: {sorted(both)}"


def test_no_decision_outlives_its_field():
    stale = (DESCRIBED | OMITTED_FIELDS) - ALL_FIELDS
    assert not stale, f"decisions for fields that no longer exist: {sorted(stale)}"


def test_descriptions_name_the_block_their_field_lives_in():
    by_block = {
        Block.MEASUREMENTS: set(MEASUREMENT_FIELDS),
        Block.ENERGY: set(ENERGY_FIELDS),
        Block.IDENTITY: set(IDENTITY_FIELDS),
    }
    wrong = [d.key for d in SENSOR_DESCRIPTIONS if d.key not in by_block[d.block]]
    assert not wrong


def test_the_thirteen_legacy_ids_map_to_their_fields():
    assert LEGACY_UNIQUE_IDS == LEGACY
    assert set(LEGACY_UNIQUE_IDS.values()) <= DESCRIBED


def test_legacy_sensors_are_enabled_and_keep_their_units():
    """kW and kWh, as the YAML declared them - a unit change would rescale
    the statistics Home Assistant already holds.

    The state class is deliberately not pinned here: `active_energy_total` is a
    balance of import against export, so it belongs to TOTAL rather than
    TOTAL_INCREASING. That changes how the recorder reads new values and leaves
    the recorded ones untouched, while a unit change would not.
    """
    adopted = set(LEGACY_UNIQUE_IDS.values())
    for description in SENSOR_DESCRIPTIONS:
        if description.key not in adopted:
            continue
        assert description.entity_registry_enabled_default is True
        if description.device_class is SensorDeviceClass.POWER:
            assert description.native_unit_of_measurement == "kW"
        if description.device_class is SensorDeviceClass.ENERGY:
            assert description.native_unit_of_measurement == "kWh"


def test_tariff_and_quadrant_counters_are_disabled_by_default():
    for description in SENSOR_DESCRIPTIONS:
        key = description.key
        is_split = (
            any(key.endswith(s) for s in ("_t1", "_t2", "_t3", "_t4")) or "_q" in key
        )
        if is_split and description.block is Block.ENERGY:
            assert description.entity_registry_enabled_default is False, key


def test_identity_sensors_are_diagnostic():
    for description in SENSOR_DESCRIPTIONS:
        if description.block is Block.IDENTITY:
            assert description.entity_category is EntityCategory.DIAGNOSTIC, (
                description.key
            )


def test_omitted_fields_are_identity_only():
    """Omission only has a defined meaning for identity words that are
    device information. A measurement or energy field is filtered out of
    SENSOR_DESCRIPTIONS by identity, not by OMITTED_FIELDS, so a genuine
    need to omit one has to be handled deliberately in the generator
    instead of by adding its name to this set."""
    assert set(IDENTITY_FIELDS) >= OMITTED_FIELDS


def test_legacy_ids_pin_to_the_registers_they_replace():
    """A wrong mapping would publish a different measurement under a
    sensor's existing history: unique_id and statistics carry over, the
    value behind them would silently change."""
    field_component = {
        **dict.fromkeys(MEASUREMENT_FIELDS, Measurements),
        **dict.fromkeys(ENERGY_FIELDS, Energy),
    }
    for legacy_id, expected_address in LEGACY_ADDRESSES.items():
        field = LEGACY_UNIQUE_IDS[legacy_id]
        component = field_component[field]
        assert address_of(component, field) == expected_address, legacy_id


# The meter's `*_total` registers hold import minus export, so they fall and go
# negative - measured on a live meter: reactive_energy_total -1206.637 with
# import 652.664 and export 1859.300. `total_increasing` promises a counter that
# only rises; Home Assistant's recorder rejects a negative state under it and
# tells the user to file a bug. Directed registers (import, export, quadrants)
# do only rise and keep that class.
NETTED = ("active_energy_total", "reactive_energy_total", "reactive_energy_total_l2")
DIRECTED = (
    "active_energy_import",
    "reactive_energy_export",
    "reactive_energy_q1",
    "reactive_energy_q4_t2",
)


def _by_key(key: str) -> WagoSensorDescription:
    return next(d for d in SENSOR_DESCRIPTIONS if d.key == key)


@pytest.mark.parametrize("key", NETTED)
def test_a_netted_counter_may_fall_and_is_not_total_increasing(key):
    """A balance of import against export is not a rising counter."""
    assert _by_key(key).state_class is SensorStateClass.TOTAL


@pytest.mark.parametrize("key", DIRECTED)
def test_a_directed_counter_stays_total_increasing(key):
    """Import, export and the quadrants only ever rise."""
    assert _by_key(key).state_class is SensorStateClass.TOTAL_INCREASING
