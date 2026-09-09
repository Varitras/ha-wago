"""Install the Modbus requirements of the Home Assistant that is installed.

Importing this integration imports `homeassistant.components.modbus`, which
imports `pymodbus` at module level - long before a test can run Home Assistant's
setup and let it install its own integration requirements. Nothing else in the
test environment brings `pymodbus` in: it is a requirement of core's modbus
INTEGRATION, not a dependency of the homeassistant distribution, and
pytest-homeassistant-custom-component does not pull it either. A fresh
environment therefore dies during collection with ModuleNotFoundError, while a
development venv that happens to hold a stray `pymodbus` from other work stays
green and hides it.

The versions are read from the installed core manifest rather than kept in a
list here. requirements.txt mirrors manifest.json's
`modbus-connection[tmodbus]>=4.10.0`, and that bound stays open on purpose - a
custom integration that pins what core pins cannot be installed beside it - so
pip is free to resolve a newer release than the Home Assistant under test ships
(4.11.1 against core's 4.10.0, when this was written) and the suite would
exercise a library version production never sees. A second, hand-kept version
list would age silently; the manifest of the release actually installed cannot.

This is a test-environment concern only. It changes nothing this integration
declares at runtime.

Run after Home Assistant is installed, before collection and before mypy.
Installs into the interpreter that runs it, so point that interpreter at the
environment you want equipped.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import homeassistant

MANIFEST = (
    pathlib.Path(homeassistant.__file__).parent / "components/modbus/manifest.json"
)


def core_modbus_requirements() -> list[str]:
    """The requirement strings core's modbus integration declares."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["requirements"]


def main() -> int:
    requirements = core_modbus_requirements()
    if not requirements:
        print(
            f"{MANIFEST} declares no requirements. Either core stopped needing "
            "pymodbus - then delete this script - or the manifest was not read "
            "correctly. Refusing to continue on an environment nothing equipped.",
            file=sys.stderr,
        )
        return 1
    # Echoed, not silent: "which pymodbus did this run actually use?" is the
    # first question when a fresh environment disagrees with a local one.
    # flush: without it this line lands after pip's own output in a log file,
    # reading as if pip had chosen the versions rather than the manifest.
    print(f"core Modbus requirements: {' '.join(requirements)}", flush=True)
    return subprocess.call([sys.executable, "-m", "pip", "install", *requirements])


if __name__ == "__main__":
    sys.exit(main())
