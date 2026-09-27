# WAGO 879 Energy Meter

A Home Assistant custom integration for the WAGO 879-3000 energy meter
fitted with the **879-9000** Modbus TCP/RTU module (see
[Supported devices](#supported-devices)).
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
  unit id, CT ratio (when the meter reports one), rated current, and a few
  operational counters.

### Entity visibility

| Group | Default state | Notes |
|---|---|---|
| Measurements (voltage, current, frequency, power, power factor) | enabled | regular sensors |
| Energy: import and export counters and their per-phase splits | enabled | `total_increasing`, kept in kWh/kvarh to match the statistics already recorded by the replaced YAML setup |
| Energy: netted totals (import minus export) and their per-phase splits | enabled | `total`, because a feed-in surplus makes them fall or go negative; same kWh/kvarh units |
| Energy: per-tariff (T1-T4) and reactive per-quadrant (Q1-Q4) split counters, day counters (total and per phase) | **disabled by default** | a direct-connected meter without tariff switching holds zero in most of these; enable per entity if needed |
| Energy: `tariff` (the active tariff word) | enabled, **diagnostic** | |
| Identity: rated current, power-down counter, phase quadrants | **disabled by default**, diagnostic | rarely useful day to day |
| Identity: CT ratio, Modbus unit id, current quadrant | enabled, **diagnostic** | |

Voltage average, current average and the CT ratio sit on registers the WAGO
manual shades grey; a direct-measuring meter leaves them at zero. They are
created only when the meter reports a non-zero voltage average or CT ratio at
setup - current average follows voltage average, since 0 A is a real reading
at no load. On a meter that leaves them empty they do not appear, and sensors
created by an earlier version show as no longer provided and can be deleted.

The serial number, meter code, protocol version and the firmware and hardware
versions are not entities at any enablement level. Serial number, firmware and
hardware version appear on the device card; meter code and protocol version are
read at setup but not surfaced.

## Supported devices

- **Tested:** the WAGO 879-3000 in its 4PU variant (direct measuring, meter
  code 1111) behind an 879-9000 module, over Modbus TCP.
- **Expected to work, untested:** the 4PS and 2PU CT variants. WAGO documents
  one register map for all three; a variant that fills registers the 4PU
  leaves empty gets the matching sensors (voltage and current average, CT
  ratio), see [Entity visibility](#entity-visibility).
- **Not supported:** other WAGO meters, and the 879-9000 over Modbus RTU.

## Use cases

- **Energy dashboard** - use *Active energy import* as grid consumption and
  *Active energy export* as return to grid. The netted *Active energy total*
  can fall, so it is not the right source for the dashboard.
- **Phase balance** - compare current and active power per phase to spot a
  phase that carries most of the load.
- **Supply quality** - watch the phase and line voltages and the frequency,
  and be told when they leave the tolerated band.

## Examples

Be notified when a phase voltage stays below the lower limit of EN 50160
(230 V - 10 %) for a minute:

```yaml
automation:
  - alias: "Low voltage on L1"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.wago_3456_voltage_l1
        below: 207
        for: "00:01:00"
    actions:
      - action: persistent_notification.create
        data:
          message: "Voltage L1 is {{ states('sensor.wago_3456_voltage_l1') }} V"
```

A direct-measuring meter does not report a voltage average. The **Min/Max**
helper (Settings -> Devices & services -> Helpers) builds one from the three
phase voltages, set to *arithmetic mean*.

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
   integration** and fill in:

   | Field | What to enter |
   |---|---|
   | Host | IP address or host name of the 879-9000 module |
   | Port | TCP port of the module; 502 unless it was changed there |
   | Modbus unit id | the address set on the meter itself (factory setting 1) |
   | Poll interval for measurements | seconds between reads of voltage, current, power; default 15 |
   | Poll interval for energy counters | seconds between reads of the counters; default 300 |

   The integration reads the meter's serial number before it saves anything,
   so a wrong address or unit id is reported right away. Host, port and unit
   id can be changed later with **Reconfigure**, the intervals under
   **Configure**.

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
integration serves further WAGO meters. The config entry carries the same
name, so nothing shown in the integrations list, in an entity id or in a
friendly name depends on an address that changes when the meter moves. An
entry from an earlier version that was titled with the address is renamed
once on the next start; a title you chose yourself is kept.

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

A poll that fails keeps the last values; only the fourth failed poll in a row
marks the entities unavailable, and the next good poll brings them back. A
meter whose link is down is logged at *info*, not as an error.

## Known limitations

- **Read-only.** The integration never writes to the meter: switching the
  tariff, resetting the day counters or changing Modbus settings is not
  possible from Home Assistant.
- **Only the 4PU variant is tested** (see [Supported devices](#supported-devices)).
- **CT ratio** is shown as the two raw words the meter sends. The manual
  prints its example in a way that leaves open whether they are decimal or
  hexadecimal, and no CT meter was available to settle it.
- **Registers the manual shades grey** get a sensor only when the meter
  reports a non-zero value at setup; a meter that starts filling them later
  needs the integration reloaded.
- **One meter per entry**; several meters mean several entries, each with
  its own host or unit id.

## Troubleshooting

- **"The meter did not answer"** when adding it - check the host and port
  (Modbus TCP uses 502) and that the 879-9000 module is reachable from Home
  Assistant; the Modbus unit id has to match the one set on the meter.
- **"Another integration already uses this address with different link
  settings"** - another Modbus configuration talks to the same address with a
  different framer or settings. Remove the duplicate.
- **Setup keeps retrying after a migration** - Home Assistant shows a repair
  issue under **Settings -> System -> Repairs** saying what is in the way,
  usually a `modbus:` block that is still loaded.
- **"answers as serial ..., but this entry belongs to serial ..."** - a
  different meter answers at the saved address. Use **Reconfigure** to point
  the entry at the right address, or add the other meter as its own entry.
- **More detail** - enable debug logging for the integration:

  ```yaml
  logger:
    logs:
      custom_components.wago_879: debug
  ```

  The integration's **Download diagnostics** gives the readings and settings
  with the address and serial number removed, ready to attach to an issue.

## Removal

1. **Settings -> Devices & services -> WAGO 879 Energy Meter**, open the
   entry's menu and choose **Delete**. This removes the device and its
   entities; the history recorded under their entity ids stays in the
   database until the recorder purges it.
2. If it was installed through HACS, remove it there and restart Home
   Assistant.

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

Every gate that can run locally runs through one script:

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

Three more checks run **only on GitHub**, because they need its runners or
actions: HACS validation (`validate.yml`), hassfest (`hassfest.yaml`) and
CodeQL (`codeql.yml`). A green `check.sh` therefore says nothing about them.

CI (see `.github/workflows/`) runs the gates of `check.sh` as separate jobs,
always against both Home Assistant versions, plus those three - on every push
to a branch other than Dependabot's and on every pull request; HACS
validation and hassfest also run nightly, CodeQL weekly.

A fresh clone of this repository has **no pre-push hook** - the hook is a
local, untracked convenience, not part of the repository. CI is the portable
twin every clone gets regardless of local setup.
