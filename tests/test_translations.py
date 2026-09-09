"""Every entity key and every flow message has a text in every language."""

import json
import pathlib

import pytest

pytest.importorskip("homeassistant")

from custom_components.wago_879.entity_descriptions import SENSOR_DESCRIPTIONS

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "wago_879"
LANGUAGES = sorted((PACKAGE / "translations").glob("*.json"))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.stem)
def test_every_sensor_key_has_a_name(language):
    names = _load(language)["entity"]["sensor"]
    missing = [
        d.translation_key for d in SENSOR_DESCRIPTIONS if d.translation_key not in names
    ]
    assert not missing, f"{language.name}: {missing}"


def test_strings_json_equals_the_english_translation():
    assert _load(PACKAGE / "strings.json") == _load(
        PACKAGE / "translations" / "en.json"
    )


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.stem)
def test_every_language_has_the_same_keys_as_english(language):
    def keys(node, prefix=""):
        if isinstance(node, dict):
            return {
                k for key, value in node.items() for k in keys(value, f"{prefix}{key}.")
            }
        return {prefix}

    assert keys(_load(language)) == keys(_load(PACKAGE / "translations" / "en.json"))
