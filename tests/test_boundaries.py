"""Register addresses and field types appear only inside wago_879_api.

The layout has one owner. A hex address or a FloatField outside that package
is a second copy of the map, and the second copy is the one that drifts.
"""

import ast
import pathlib
import re

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "wago_879"
API = PACKAGE / "wago_879_api"
FIELD_TYPES = {"FloatField", "NumberField", "RawField", "StringField"}
REGISTER_LITERAL = re.compile(r"0x[4-6][0-9A-Fa-f]{3}\b")


def _outside_api():
    return [p for p in PACKAGE.rglob("*.py") if API not in p.parents]


def test_no_field_type_is_used_outside_the_api_package():
    offenders = []
    for source in _outside_api():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in FIELD_TYPES:
                offenders.append(f"{source.name}: {node.id}")
    assert not offenders, f"register fields outside wago_879_api: {offenders}"


def test_no_register_address_literal_outside_the_api_package():
    offenders = [
        f"{source.name}: {m.group()}"
        for source in _outside_api()
        for m in REGISTER_LITERAL.finditer(source.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"register addresses outside wago_879_api: {offenders}"


def test_the_scan_catches_a_copied_address():
    assert REGISTER_LITERAL.search("value = read(0x5002)")
    assert not REGISTER_LITERAL.search("colour = 0xFFFFFF")
