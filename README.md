# WAGO 879 Energy Meter

A Home Assistant custom integration for the WAGO 879-3000 energy meter
(4PU/4PS/2PU CT variants) fitted with the **879-9000** Modbus TCP/RTU module.
It reads the meter over Modbus TCP through Home Assistant's core `modbus`
integration and **never writes** to it - there is no register on this meter
that this integration sets.

## What it reads

Three groups of registers. The two measured groups each have their own poll
interval; the identity group is read once, when the entry is set up.

- **Measurements** - per-phase and total voltage, current, frequency, active,
  reactive and apparent power, power factor.
- **Energy** - total, per-phase and per-tariff (T1-T4) active and reactive
  energy counters, the reactive-only per-quadrant (Q1-Q4) split, and the
  running day counters (total and per phase).
- **Identity** - serial number, meter code, firmware/hardware version, Modbus
  unit id, CT ratio, rated current, and a few operational counters.

### Entity visibility

| Group | Default state | Notes |
|---|---|---|
| Measurements (voltage, current, frequency, power, power factor) | enabled | regular sensors |
| Energy: total, import and export counters and their per-phase splits | enabled | `total_increasing`, kept in kWh/kvarh to match the statistics already recorded by the replaced YAML setup |
| Energy: per-tariff (T1-T4) and reactive per-quadrant (Q1-Q4) split counters, day counters (total and per phase) | **disabled by default** | a direct-connected meter without tariff switching holds zero in most of these; enable per entity if needed |
| Energy: `tariff` (the active tariff word) | enabled, **diagnostic** | |
| Identity: rated current, power-down counter, phase quadrants | **disabled by default**, diagnostic | rarely useful day to day |
| Identity: CT ratio, Modbus unit id, current quadrant | enabled, **diagnostic** | |

The serial number, meter code, protocol version and the firmware and hardware
versions are not entities at any enablement level. Serial number, firmware and
hardware version appear on the device card; meter code and protocol version are
read at setup but not surfaced.

## Requirements

- Home Assistant **2026.9** or newer (the minimum this integration is tested
  against; see `hacs.json`).
- A WAGO 879-3000 meter fitted with the **879-9000** Modbus module, reachable
  over TCP.

## Installation (HACS)

1. In HACS, add this repository as a custom repository (category:
   Integration) if it is not already listed.
2. Install "WAGO 879 Energy Meter" and restart Home Assistant.
3. Add the integration from **Settings -> Devices & services -> Add
   integration** and enter the meter's host, port and Modbus unit id.

## Migrating from a YAML `modbus:` block

If the meter was previously set up through a YAML `modbus:` block (the usual
way before this integration existed):

1. **Back up** your configuration.
2. **Remove** the meter's `modbus:` block from `configuration.yaml` (or the
   file it was split into).
3. **Restart** Home Assistant.
4. **Add the integration** through the UI as above, using the same host.

On its first setup the integration finds the thirteen sensor entities the
YAML block registered, adopts their existing entity ids, and removes the old
YAML-backed registry entries - the corresponding entities keep their entity
id and their recorded history/statistics rather than starting over under a
new id. Setup is refused, unadopted, if any of those legacy entities are
still live (i.e. the YAML block was not actually removed).

After step 3 Home Assistant shows those entities as "unavailable" (restored)
until the integration adopts them; that is expected and does not block the
adoption. Only an entity a platform is really serving does.

## Entity naming

The device is called `WAGO <last four digits of the serial>`, and every
entity is named after it: `sensor.wago_3456_voltage_l1`, shown as
"WAGO 3456 Voltage L1". The model is not part of the name - it belongs on the
device card, and naming entities after one model would age badly once this
integration serves further WAGO meters. The config entry itself is still
titled with the meter's address, so two meters are easy to tell apart in the
integrations list, but no entity id or friendly name depends on an address
that changes when the meter moves.

Entities adopted from a YAML `modbus:` block keep their old entity ids, as
described above.

Earlier versions had no device name and so built entity ids from the entry
title, i.e. from the meter's address (`sensor.192_0_2_10_voltage_l1`). Those
ids are renamed once, on the next setup, and their recorded history and
statistics move with them. Only ids in exactly that generated form are
touched: an adopted id and an id renamed by hand are left as they are, and an
id whose new name is already taken keeps its old one, with a warning in the
log.

## Poll intervals

The two measured groups have independent, configurable intervals, matching
what the replaced YAML block used; the identity group is read once at setup
and has no interval:

- **Measurement interval** - the instantaneous values (voltage, current,
  power, ...); default 15 seconds.
- **Energy interval** - the energy counters; default 300 seconds.

Both can be changed from the integration's options, within 5-3600 seconds.

## Setting up a development environment

```sh
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python .github/scripts/install_core_modbus_requirements.py
```

The second command brings Home Assistant, the test plugin and the gate tools;
the third adds what core's `modbus` integration needs (`pymodbus` and friends).
That step is separate because those versions are not this repository's to
choose: `manifest.json` declares `modbus-connection[tmodbus]>=4.10.0` with a
deliberately open bound, and the exact pins are read from the manifest of the
Home Assistant release that just got installed. Without it, importing the
integration fails with `ModuleNotFoundError: No module named 'pymodbus'` before
the first test runs.

`check.sh` runs the same step first, so an environment built this way and one
that has drifted end up equal either way.

## Running the gates

Every gate this repository ships runs through one script:

```sh
PYTHON=/path/to/venv/bin/python .github/scripts/check.sh
```

It is a POSIX shell script, so on Windows it is run through WSL rather than
from PowerShell.

It runs, in order: `ruff check`, `ruff format --check`, `mypy`, `pip-audit`,
`gitleaks` (over the full history, using `.gitleaks.toml`), the test suite
with coverage, and a mutation-testing pass that verifies the tests actually
fail when the code they guard is broken. An optional second Home Assistant
version can be checked too:

```sh
MIN_HA_PYTHON=/path/to/min-ha-venv/bin/python .github/scripts/check.sh
```

Without `MIN_HA_PYTHON` that run is skipped, and the script's final line says
so - a skipped gate that announces itself is honest, one that passes silently
is not.

A fresh clone of this repository has **no pre-push hook** - the hook is a
local, untracked convenience, not part of the repository. CI (see
`.github/workflows/`) runs the same gates as `check.sh` on every push and
pull request, and is the portable twin every clone gets regardless of local
setup.
