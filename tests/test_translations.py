"""Every entity key and every flow message has a text in every language."""

import ast
import json
import pathlib
import re

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


def _flow_steps(strings):
    for section in ("config", "options"):
        yield from strings[section]["step"].items()


@pytest.mark.parametrize("language", LANGUAGES, ids=lambda p: p.stem)
def test_every_form_field_explains_itself(language):
    """The quality scale's config-flow rule: a field label alone leaves the
    user guessing - which address, which unit id, why two intervals."""
    missing = [
        f"{step}.{field}"
        for step, texts in _flow_steps(_load(language))
        for field in texts.get("data", {})
        if field not in texts.get("data_description", {})
    ]
    assert not missing, f"{language.name}: {missing}"


# What Home Assistant shows a user or writes to the log on this package's
# behalf; the quality scale's exception-translations rule.
TRANSLATED_EXCEPTIONS = {
    "ConfigEntryError",
    "ConfigEntryNotReady",
    "DeviceUnreachable",
    "HomeAssistantError",
    "ServiceValidationError",
    "UpdateFailed",
}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _called_name(call: ast.Call) -> str | None:
    """`ConfigEntryNotReady(...)` and `exceptions.ConfigEntryNotReady(...)` alike."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _calls_in(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_name(node) in TRANSLATED_EXCEPTIONS:
            yield node


def _constructions():
    """Every call of a translated exception class in the package."""
    for source in sorted(PACKAGE.rglob("*.py")):
        for call in _calls_in(ast.parse(source.read_text(encoding="utf-8"))):
            yield source.name, call


def _untranslated(call: ast.Call, messages: dict) -> str | None:
    """Why `call` would not render its translation, or None when it does.

    Each check is a way the message silently went wrong once it was built:
    without the domain core shows no text at all ("None" in the setup
    message), a missing placeholder shows up literally as `{host}`, and a
    placeholder dict held in a variable is a dict nobody here can read.
    """
    keywords = {k.arg: k.value for k in call.keywords}
    key = keywords.get("translation_key")
    if call.args or not isinstance(key, ast.Constant):
        return "has no literal translation_key"
    domain = keywords.get("translation_domain")
    if not (isinstance(domain, ast.Name) and domain.id == "DOMAIN"):
        return "does not pass translation_domain=DOMAIN"
    if key.value not in messages:
        return f"{key.value} not in strings.json"
    wanted = set(PLACEHOLDER.findall(messages[key.value]["message"]))
    given = keywords.get("translation_placeholders")
    if given is None:
        passed = set()
    elif isinstance(given, ast.Dict):
        passed = {k.value for k in given.keys if isinstance(k, ast.Constant)}
    else:
        return f"{key.value}: placeholders are not a literal dict"
    if passed != wanted:
        return f"{key.value}: {passed} != {wanted}"
    return None


def test_every_raised_message_is_translated():
    messages = _load(PACKAGE / "strings.json").get("exceptions", {})
    offenders = [
        f"{name}:{call.lineno} {reason}"
        for name, call in _constructions()
        if (reason := _untranslated(call, messages)) is not None
    ]
    assert not offenders


def test_the_translation_scan_catches_the_shapes_it_was_written_for():
    messages = {
        "meter_not_answering": {"message": "The meter at {host} did not answer"}
    }

    def reason(source: str) -> str | None:
        return _untranslated(next(_calls_in(ast.parse(source))), messages)

    assert reason('raise ConfigEntryNotReady("text")') is not None
    assert reason('raise exceptions.ConfigEntryNotReady("text")') is not None
    assert reason('raise ServiceValidationError("text")') is not None
    assert "translation_domain" in reason(
        'raise ConfigEntryNotReady(translation_key="meter_not_answering",'
        ' translation_placeholders={"host": host})'
    )
    assert "!=" in reason(
        "raise ConfigEntryNotReady(translation_domain=DOMAIN,"
        ' translation_key="meter_not_answering")'
    )
    assert "literal dict" in reason(
        "raise ConfigEntryNotReady(translation_domain=DOMAIN,"
        ' translation_key="meter_not_answering", translation_placeholders=values)'
    )
    assert (
        reason(
            "raise ConfigEntryNotReady(translation_domain=DOMAIN,"
            ' translation_key="meter_not_answering",'
            ' translation_placeholders={"host": host})'
        )
        is None
    )


def test_every_sensor_without_a_device_class_has_an_icon():
    """The quality scale's icon-translations rule: a device class brings its
    own icon, everything else needs one in icons.json - and icons.json may not
    name a key no sensor uses."""
    icons = _load(PACKAGE / "icons.json")["entity"]["sensor"]
    keys = {d.translation_key for d in SENSOR_DESCRIPTIONS}
    unnamed = [
        d.translation_key
        for d in SENSOR_DESCRIPTIONS
        if d.device_class is None and d.translation_key not in icons
    ]
    assert not unnamed
    assert set(icons) <= keys


def test_every_refused_adoption_has_a_repair_text():
    """Setup turns a refused adoption into a repair issue with the refusal's
    own key and placeholders, so each one needs a title and description that
    use exactly those placeholders."""
    strings = _load(PACKAGE / "strings.json")
    issues = strings.get("issues", {})
    offenders = []
    for name, call in _constructions():
        if name != "migration.py":
            continue
        key = next(k.value.value for k in call.keywords if k.arg == "translation_key")
        wanted = set(PLACEHOLDER.findall(strings["exceptions"][key]["message"]))
        if key not in issues:
            offenders.append(f"{key}: no repair text")
            continue
        used = set(PLACEHOLDER.findall(issues[key]["description"]))
        if used != wanted:
            offenders.append(f"{key}: {used} != {wanted}")
    assert not offenders
