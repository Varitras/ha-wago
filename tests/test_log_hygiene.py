"""No device identifier reaches a log line or a user-facing message unmasked.

A Home Assistant log is what users attach to a GitHub issue, and the text of a
`ConfigEntryError` travels the same way, so both are scanned here. Behaviour
tests only ever pin the one message being written at the time; this pins every
message the package can produce, including the ones added next year.

ponytail: a name heuristic with a known ceiling - it recognises identifiers by
what they are called, so a device address carried in a variable named `target`
walks past it. Renaming to evade the guard is possible and deliberate: the
alternative is tracking values across the package, which is a type checker's
job, not a guard's.
"""

import ast
import keyword
import pathlib
import re

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "wago_879"

# Word parts that name something identifying a physical device or its owner.
SUSPICIOUS_PARTS = {
    "address",
    "host",
    "hostname",
    "ip",
    "mac",
    "password",
    "serial",
    "token",
    "unique",
}
MASK = "mask"

# Identifiers allowed through, and why. Named here rather than dropped from
# SUSPICIOUS_PARTS so the word keeps guarding every other site.
EXEMPTIONS: dict[str, dict[str, str]] = {}

WORD_PART = re.compile(r"[A-Z]?[a-z0-9]+")


def _parts(name: str) -> set:
    parts = set(name.lower().split("_"))
    parts |= {p.lower() for p in WORD_PART.findall(name)}
    return parts - set(keyword.kwlist)


def _suspicious_names(node) -> set:
    """Every identifier under `node` whose name says "device identifier"."""
    found = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            name = inner.id
        elif isinstance(inner, ast.Attribute):
            name = inner.attr
        else:
            continue
        if _parts(name) & SUSPICIOUS_PARTS:
            found.add(name)
    return found


def _masked_names(node) -> set:
    """The identifiers `node` passes through the masking function."""
    found = set()
    for inner in ast.walk(node):
        is_mask_call = isinstance(inner, ast.Call) and (
            (isinstance(inner.func, ast.Name) and inner.func.id == MASK)
            or (isinstance(inner.func, ast.Attribute) and inner.func.attr == MASK)
        )
        if is_mask_call:
            for argument in inner.args:
                found |= _suspicious_names(argument)
    return found


def _names_bound_to_a_masked_value(tree) -> set:
    """`safe = mask(host)` - the binding counts as masked wherever it is used."""
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _masked_names(node.value):
            bound |= {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
    return bound


def _reported_calls(tree):
    """The calls whose text a user or an issue report gets to read."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            yield node.exc
            continue
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        writes_a_record = (
            isinstance(receiver, ast.Name) and receiver.id.upper().endswith("LOGGER")
        ) or (isinstance(receiver, ast.Attribute) and receiver.attr == "logger")
        if writes_a_record:
            yield node


def leaks(source: str) -> set:
    """The identifiers `source` reports without masking them first."""
    tree = ast.parse(source)
    safe = _names_bound_to_a_masked_value(tree)
    found = set()
    for call in _reported_calls(tree):
        arguments = [*call.args, *(k.value for k in call.keywords)]
        for argument in arguments:
            found |= _suspicious_names(argument) - _masked_names(argument) - safe
    return found


def test_no_reported_message_names_a_device_identifier():
    offenders = []
    for source in sorted(PACKAGE.rglob("*.py")):
        allowed = EXEMPTIONS.get(source.name, {})
        for name in sorted(leaks(source.read_text(encoding="utf-8"))):
            if name not in allowed:
                offenders.append(f"{source.name}: {name}")

    assert not offenders, (
        f"{offenders} reach a log line or a user-facing message unmasked - "
        "wrap them in logging_policy.mask(), or add an entry to EXEMPTIONS "
        "here saying why the full value has to be readable."
    )


def test_the_scan_catches_the_lines_it_was_written_for():
    """The three leaks this guard was written for, verbatim as they read before
    the fix, and the fixed forms that have to pass."""
    not_ready = 'raise ConfigEntryNotReady(f"{params.host}: {err}") from err'
    assert leaks(not_ready) == {"host"}
    assert (
        leaks('raise ConfigEntryNotReady(f"{mask(params.host)}: {err}") from err')
        == set()
    )

    mismatch = (
        "raise ConfigEntryError(\n"
        '    f"{params.host} answers as meter {serial}, but this entry belongs to "\n'
        '    f"meter {entry.unique_id}. Point the entry at the address of meter "\n'
        '    f"{entry.unique_id} with Reconfigure, or add meter {serial} as its "\n'
        '    "own entry."\n'
        ")"
    )
    assert leaks(mismatch) == {"host", "serial", "unique_id"}

    # The coordinator name is the entry title, and the title is the host: this
    # is the shape the guard cannot see, and the reason the fix moved the name
    # to the masked serial instead of trusting a scan to notice.
    assert leaks('_LOGGER.error("%s: poll failed", self.name)') == set()

    assert leaks('_LOGGER.debug("connected to %s", hostname)') == {"hostname"}
    assert leaks('_LOGGER.debug("connected to %s", mask(hostname))') == set()

    bound = 'shown = mask(serial)\n_LOGGER.info("meter %s is back", shown)'
    assert leaks(bound) == set()

    assert leaks('_LOGGER.debug("read %d registers", count)') == set()


def test_every_exemption_still_describes_a_real_leak():
    """An exemption for something the scan no longer flags is a licence nobody
    revoked - and the next identifier with that name inherits it."""
    stale = []
    for file_name, allowed in EXEMPTIONS.items():
        found = leaks((PACKAGE / file_name).read_text(encoding="utf-8"))
        stale += [f"{file_name}: {name}" for name in allowed if name not in found]

    assert not stale, f"exemption(s) no longer needed, remove them: {stale}"
