"""The CI workflow must test the Home Assistant release it claims to test.

A job that installs pytest-homeassistant-custom-component unpinned reads like
"newest Home Assistant" but is not: the plugin ships a normal, non-prerelease
version for every Home Assistant BETA too, so pip resolves to one pinning a
beta. Nothing then covers the release people actually run, and the job stays
green while saying "current HA".

The "minimum" job is the other end: it pins the plugin release that ships
exactly the Home Assistant version hacs.json declares, so an API that does
not exist yet on that release is caught before a user meets it.

Only the selection logic is exercised - no PyPI access, so this stays a fast
unit test rather than a network-dependent one.
"""

import importlib.util
import json
from pathlib import Path
import re

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "resolve_phcc.py"
WORKFLOW = REPO / ".github" / "workflows" / "test.yaml"
TEST_REQUIREMENTS = REPO / "requirements_test.txt"
# The gates that must judge a change the same way on a laptop and in CI.
GATE_TOOLS = ("ruff", "mypy", "pip-audit")


def _load():
    spec = importlib.util.spec_from_file_location("resolve_phcc", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_phcc = _load()


@pytest.mark.parametrize(
    ("pin", "expected"),
    [
        ("homeassistant==2026.7.4", False),
        ("homeassistant==2026.8.0b3", True),
        ("homeassistant==2026.8.0rc1", True),
        ("homeassistant==2026.8.0a1", True),
        ("homeassistant==2026.8.0.dev0", True),
        # A range is not a pin: pip would pick, and it picks betas.
        ("homeassistant>=2026.8.0", True),
        ("not a requirement at all", True),
        # No pin at all: we cannot tell what would be installed, and the whole
        # point of the script is not having to guess.
        (None, True),
    ],
)
def test_a_prerelease_pin_is_recognised(pin, expected):
    assert resolve_phcc.is_prerelease(pin) is expected


def test_the_newest_final_release_wins_over_newer_betas():
    pins = {
        "0.13.351": "homeassistant==2026.8.0b3",
        "0.13.350": "homeassistant==2026.8.0b2",
        "0.13.348": "homeassistant==2026.7.4",
        "0.13.347": "homeassistant==2026.7.3",
    }

    assert resolve_phcc.newest_stable(pins) == "0.13.348"


def test_versions_are_compared_numerically_not_as_text():
    """ "0.13.9" must not outrank "0.13.348" - which it does as a string."""
    pins = {
        "0.13.9": "homeassistant==2026.1.0",
        "0.13.348": "homeassistant==2026.7.4",
    }

    assert resolve_phcc.newest_stable(pins) == "0.13.348"


def test_nothing_usable_fails_loudly():
    """Falling back to "install whatever" would put the beta straight back."""
    with pytest.raises(SystemExit):
        resolve_phcc.newest_stable({"0.13.351": "homeassistant==2026.8.0b3"})


def test_an_explicit_requirement_is_passed_through(capsys):
    """A pinned entry must reach pip unchanged - and must not trigger a PyPI
    lookup."""
    resolve_phcc.main(
        ["resolve_phcc.py", "pytest-homeassistant-custom-component==0.13.190"]
    )

    assert capsys.readouterr().out.strip() == (
        "PHCC_SPEC=pytest-homeassistant-custom-component==0.13.190"
    )


def _steps_of(job: str) -> list:
    """The lines of one workflow job that DO something - not the comments
    that talk about it. A comment explaining why a file is NOT used satisfies
    a plain substring search for that file."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index(f"  {job}:")
    rest = workflow[start + 1 :]
    end = re.search(r"^  \w+:\s*$", rest, flags=re.MULTILINE)
    body = workflow[start : start + 1 + end.start()] if end else workflow[start:]
    return [line for line in body.splitlines() if not line.strip().startswith("#")]


def _minimum_job_label() -> str:
    labels = re.findall(
        r'- name: "(minimum[^"]*)"', WORKFLOW.read_text(encoding="utf-8")
    )
    assert len(labels) == 1, f"expected one minimum job, found {labels}"
    return labels[0]


def test_the_minimum_job_names_the_version_hacs_declares():
    """Three places say which Home Assistant is the oldest supported one -
    hacs.json, the pinned plugin release, and the job's own name. The pin can
    only be checked with PyPI (check_min_ha.py does that inside the job); the
    label can be checked here, and it is the half that lies to a reader."""
    declared = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))[
        "homeassistant"
    ]
    release = ".".join(declared.split(".")[:2])

    assert release in _minimum_job_label(), (
        f"hacs.json declares {declared} as the minimum, but the matrix job is "
        f'called "{_minimum_job_label()}". Raise the job name AND its pinned '
        "plugin release together - the pin is what decides what is tested."
    )


def test_the_minimum_job_actually_checks_the_version_it_installed():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "check_min_ha.py" in workflow, (
        "the minimum job does not verify the Home Assistant it installed, so "
        "a wrong pin runs green under the right label"
    )
    assert re.search(r"if:\s*matrix\.check-declared-minimum", workflow)
    assert "check-declared-minimum: true" in workflow, (
        "no matrix entry opts into the check, so it never runs"
    )


def test_a_patch_release_still_counts_as_the_declared_minimum():
    """hacs.json names a patch; the plugin pins whichever patch of that
    feature release it ships. Comparing them literally would fail the job for
    being right."""
    namespace: dict = {}
    source = (REPO / ".github" / "scripts" / "check_min_ha.py").read_text(
        encoding="utf-8"
    )
    body = source[source.index("def feature_release") : source.index("def main")]
    exec(compile(body, "check_min_ha.py", "exec"), namespace)  # noqa: S102
    feature_release = namespace["feature_release"]

    assert feature_release("2026.9.4") == feature_release("2026.9.0")
    assert feature_release("2026.10.0") != feature_release("2026.9.0")


def test_the_test_job_resolves_a_final_home_assistant():
    steps = _steps_of("pytest")

    assert any("resolve_phcc.py" in line for line in steps), (
        "the test job installs the plugin some other way than through the "
        "resolver, so it can land on a Home Assistant beta"
    )
    assert any('phcc: "latest-stable-ha"' in line for line in steps), (
        "no matrix entry asks the resolver for the newest final release"
    )
    assert not [line for line in steps if "requirements_test.txt" in line], (
        "the unpinned requirements file is back in the test job"
    )


def test_the_test_job_runs_the_whole_suite_and_the_mutations():
    steps = _steps_of("pytest")

    assert any('pytest tests/ -q -m ""' in line for line in steps), (
        'the e2e tests are deselected by default; CI has to pass -m ""'
    )
    assert any("mutate.py .github/mutations" in line for line in steps), (
        "a green suite says the tests pass, not that they would catch anything"
    )


def test_the_type_check_runs_the_same_home_assistant_as_the_test_job():
    """A checker that vouches for another release vouches for nothing."""
    steps = _steps_of("mypy")

    assert any("resolve_phcc.py latest-stable-ha" in line for line in steps), (
        "the type check resolves its Home Assistant some other way than the "
        "job whose type surface it is supposed to be checking"
    )
    # Reading one pinned line out of requirements_test.txt is fine; installing
    # the whole file is not - it carries the unpinned plugin.
    assert not [line for line in steps if "-r requirements_test.txt" in line], (
        "the unpinned requirements file is installed wholesale, so this job "
        "can pull a Home Assistant beta the tests never run against"
    )


@pytest.mark.parametrize("job", ["pytest", "mypy"])
def test_every_job_that_imports_the_integration_equips_it(job):
    """Importing this integration imports core's modbus, which imports pymodbus
    at module level. Nothing the jobs install brings pymodbus in, so a job
    without this step dies during collection - and a job that keeps it while
    its sibling loses it makes the two ends of the matrix disagree about which
    Modbus library is under test. test_guards.py holds the other half: the same
    step in .github/scripts/check.sh."""
    steps = _steps_of(job)

    assert any("install_core_modbus_requirements.py" in line for line in steps), (
        f'the "{job}" job imports the integration without installing the Modbus '
        "requirements of the Home Assistant it just installed"
    )


def test_the_workflow_still_runs_ruff_pinned():
    """Formatting that only one machine checks survives until the first
    commit written somewhere else; an unpinned Ruff enforces whatever it
    decided this week. The pin itself lives in requirements_test.txt."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "run: ruff check ." in workflow
    assert "run: ruff format --check ." in workflow
    assert _reads_pin_from_requirements(workflow, "ruff")


def test_the_secret_scan_in_ci_uses_the_repositorys_own_rules():
    """check.sh and the pre-push hook scan with .gitleaks.toml; the action
    does not read it unless told, so CI would otherwise vouch for a rule set
    no local run ever exercised."""
    steps = _steps_of("gitleaks")

    assert any("GITLEAKS_CONFIG:" in line for line in steps), (
        "the gitleaks job does not name .gitleaks.toml, so CI scans with "
        "different rules than check.sh and the pre-push hook"
    )
    assert any(".gitleaks.toml" in line for line in steps)


def _pins_of(text: str) -> dict[str, str]:
    """Every `tool==version` in a text, whatever quotes it sits in."""
    return {
        tool: version
        for tool, version in re.findall(r"([a-z-]+)==([\d.]+)", text)
        if tool in GATE_TOOLS
    }


def _reads_pin_from_requirements(workflow: str, tool: str) -> bool:
    """Whether the workflow installs `tool` at the version requirements_test.txt names."""
    return f"grep -E '^{tool}==' requirements_test.txt" in workflow


def test_every_gate_has_one_pin_and_the_workflow_reads_it():
    """One pin per tool, in requirements_test.txt, and CI takes it from there.

    The workflow used to carry its own copy of each version. Dependabot reads
    requirements files and not run: steps, so its first bump raised one copy
    and left the other - a formatter in CI that reflows differently from the
    one deciding locally, a red build nobody changed. A literal version for a
    gate tool in the workflow is that second copy coming back.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    locally = _pins_of(TEST_REQUIREMENTS.read_text(encoding="utf-8"))

    assert set(locally) == set(GATE_TOOLS), (
        f"requirements_test.txt must pin every gate tool with ==: {locally}"
    )
    literal_in_ci = _pins_of(workflow)
    assert not literal_in_ci, (
        f"the workflow pins {literal_in_ci} itself - read the version from "
        "requirements_test.txt instead, so Dependabot moves the only copy"
    )
    not_read = [
        tool for tool in GATE_TOOLS if not _reads_pin_from_requirements(workflow, tool)
    ]
    assert not not_read, (
        f"the workflow does not take {not_read} from requirements_test.txt - "
        "an unpinned install enforces whatever the tool released this week"
    )


def test_the_pin_reader_matches_the_workflow_line_exactly():
    """Proof-of-red for the helper above: the shape it looks for, and a
    near miss that must not count."""
    exact = "run: pip install \"$(grep -E '^ruff==' requirements_test.txt)\""
    near_miss = 'run: pip install "$(grep ruff requirements_test.txt)"'
    assert _reads_pin_from_requirements(exact, "ruff")
    assert not _reads_pin_from_requirements(near_miss, "ruff")


def test_the_step_reader_skips_comments_and_stops_at_the_next_job():
    """Proof-of-red for the helper the checks above rely on."""
    steps = _steps_of("mypy")

    assert steps and steps[0].strip() == "mypy:"
    assert not any(line.strip().startswith("#") for line in steps)
    assert not any(line.strip() == "pytest:" for line in steps), (
        "the reader ran into the next job"
    )
