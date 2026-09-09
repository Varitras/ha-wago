#!/bin/sh
# Every gate this repository ships, in one command.
#
# Run this before calling a change done; CI runs the same set, and a guard in
# tests/test_guards.py fails if the two ever drift apart. The order is cheap to
# expensive, stopping at the first failure.
#
# Anything machine-local arrives through the environment, never as a path in
# this file:
#
#     PYTHON=/path/to/venv/bin/python .github/scripts/check.sh

set -e

PYTHON="${PYTHON:-python}"

# Not a gate - the one thing this script INSTALLS into $PYTHON, before any gate
# imports the integration. Importing it imports core's modbus, which imports
# pymodbus at module level, and nothing in requirements_test.txt brings pymodbus
# in; a fresh venv would fail collection before the first test. The versions
# cannot be pinned in requirements_test.txt either - they are whatever the
# INSTALLED Home Assistant declares, which differs per Home Assistant release.
# See the script. Already-satisfied pins make this a no-op.
echo "== core Modbus requirements =="
"$PYTHON" .github/scripts/install_core_modbus_requirements.py

echo "== ruff =="
"$PYTHON" -m ruff check .

echo "== format =="
"$PYTHON" -m ruff format --check .

echo "== mypy =="
"$PYTHON" -m mypy

echo "== pip-audit =="
"$PYTHON" -m pip_audit -r requirements.txt --strict

# The whole history, fail-closed: a missing scanner is a failed gate, not a
# skipped one - this script says "all gates passed" and has to mean it.
echo "== gitleaks =="
if ! command -v gitleaks >/dev/null 2>&1; then
    echo "gitleaks is not installed; install it (apt/brew/winget) - the secret gate cannot be skipped" >&2
    exit 1
fi
# Two versions judged the same commits differently, so an older scanner is not
# a weaker gate but a different one: pin a floor rather than accept whatever
# the distribution ships.
GITLEAKS_MIN=8.30
GITLEAKS_FOUND=$(gitleaks version 2>/dev/null | grep -oE "[0-9]+\.[0-9]+" | head -1)
if [ -z "$GITLEAKS_FOUND" ] || [ "$(printf '%s\n' "$GITLEAKS_MIN" "$GITLEAKS_FOUND" | sort -V | head -1)" != "$GITLEAKS_MIN" ]; then
    echo "gitleaks ${GITLEAKS_FOUND:-of unknown version} found, $GITLEAKS_MIN or newer required (the Ubuntu package is older; put a current binary first in PATH)" >&2
    exit 1
fi
echo "gitleaks $GITLEAKS_FOUND"
gitleaks git --config .gitleaks.toml --redact --no-banner .

echo "== pytest =="
# -m "" cancels the `-m "not e2e"` default from pyproject.toml, so the slow
# end-to-end tests against a real Home Assistant run here too.
# --cov-fail-under: raise it when coverage rises, never lower it to get past
# a red run.
"$PYTHON" -m pytest tests/ -q -m "" --cov=custom_components/wago_879 --cov-report=term:skip-covered --cov-fail-under=97

# The second Home Assistant version is optional because its interpreter
# lives wherever you put it:
#
#     MIN_HA_PYTHON=/path/to/min-ha-venv/bin/python .github/scripts/check.sh
#
# Without it the minimum-version run is SKIPPED and says so - a skipped gate
# that announces itself is honest; one that passes silently is not.
MINIMUM_RUN="skipped"
if [ -n "$MIN_HA_PYTHON" ]; then
    MINIMUM_RUN="passed"
    # Which Home Assistant that interpreter actually holds, before spending a
    # full suite on it: a local venv is pinned by hand and ages quietly.
    echo "== minimum Home Assistant version =="
    "$MIN_HA_PYTHON" .github/scripts/check_min_ha.py

    # Its own Home Assistant, its own Modbus pins - the whole reason the
    # versions are read from the manifest instead of written down once.
    "$MIN_HA_PYTHON" .github/scripts/install_core_modbus_requirements.py

    echo "== pytest (minimum Home Assistant) =="
    "$MIN_HA_PYTHON" -m pytest tests/ -q -m ""
else
    echo "== pytest (minimum Home Assistant): SKIPPED, set MIN_HA_PYTHON =="
fi

echo "== mutations =="
"$PYTHON" .github/scripts/mutate.py .github/mutations/plan.json

echo
if [ "$MINIMUM_RUN" = "passed" ]; then
    echo "all gates passed"
else
    echo "all gates passed EXCEPT the minimum Home Assistant run, which was skipped"
fi
