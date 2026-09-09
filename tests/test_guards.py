"""The guards that watch the guards.

Two failure modes threaten a suite of structural guards, and both are quiet:

  A guard goes BLIND. It scans one source file by name, code moves to a new
  module, and the scan keeps passing over the file it still knows. The suite
  stays green about a protection that no longer looks anywhere.

  A guard goes MISSING. Deleting a test is a green diff. Nothing says the
  protection went with it.

So: no test may bind itself to a single package source file, and every guard
file has to be listed here by name.
"""

import ast
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent
REPO = TESTS.parents[0]
PACKAGE = REPO / "custom_components" / "wago_879"

# Guard files, and what each one holds. Deleting one of these files - or
# emptying it - fails the index test below. This list is the answer to "what
# stops the old problems coming back", for whoever asks in six months.
GUARD_FILES = {
    "test_boundaries.py": "register addresses and decoding live only in wago_879_api",
    "test_ci_matrix.py": "the CI workflow tests the Home Assistant release it claims to",
    "test_comment_narration.py": "no comment merely restates the code it sits on",
    "test_entity_descriptions.py": "every register carries exactly one decision and the legacy ids map 1:1",
    "test_imports.py": "every module imports outside the author's own tree",
    "test_log_hygiene.py": "no device identifier reaches a log line or a user-facing message unmasked",
    "test_requirements.py": "the manifest and requirements.txt name the same dependencies",
    "test_translations.py": "every entity key and flow message has a text in every language",
    "test_guards.py": "the guards stay package-wide and stay present",
}

# Files allowed to scan a package directory with glob("*.py") or rglob("*.py")
# without being treated as a package-wide structural guard, and why. This is
# the one legitimate case _scans_the_package would otherwise flag: naming it
# here (instead of narrowing the detector) keeps the detector strict for
# every guard written after this one.
PACKAGE_SCAN_EXEMPTIONS = {
    "test_registers.py": (
        "scans wago_879_api narrowly to check one property of that package "
        "(it imports no Home Assistant), not a package-wide structural rule"
    ),
}


def _a_source_file_of_this_package(target) -> set:
    """`<path> / "items.py"` -> {"items"}, when items.py really is one of ours.

    Matched against the PACKAGE tree rather than the literal name `PACKAGE`,
    so a guard that binds its own path to a different name is still caught.
    """
    if not (isinstance(target, ast.BinOp) and isinstance(target.op, ast.Div)):
        return set()
    name = target.right
    if not (isinstance(name, ast.Constant) and str(name.value).endswith(".py")):
        return set()
    if not any(PACKAGE.rglob(str(name.value))):
        return set()
    return {str(name.value).removesuffix(".py")}


def _tests_that_read_one_source_file(source: str):
    """The modules whose source `source` reads as ONE named file.

    Matched on the call, not on a variable name, so a path bound to a
    differently-named variable is still caught. `Path(module.__file__).parent`
    is deliberately not a hit: that resolves the package directory, which is
    the shape a package-wide scan starts from, not a single-file read.
    """
    tree = ast.parse(source)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                module = _a_source_file_of_this_package(node.value)
                if module:
                    bound[target.id] = module

    found = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_text"
        ):
            continue
        found |= _a_source_file_of_this_package(node.func.value)
        if isinstance(node.func.value, ast.Name):
            found |= bound.get(node.func.value.id, set())
        target = ast.unparse(node.func.value)
        if "__file__" not in target or ".parent" in target:
            continue
        for inner in ast.walk(node.func.value):
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "__file__"
                and isinstance(inner.value, ast.Name)
            ):
                found.add(inner.value.id)
    return found


def test_no_guard_is_pinned_to_a_single_source_file():
    """A scan that names one file goes blind the moment code moves - and a
    blind guard is worse than none, because the suite stays green."""
    offenders = []
    for test_file in sorted(TESTS.glob("test_*.py")):
        for module in _tests_that_read_one_source_file(
            test_file.read_text(encoding="utf-8")
        ):
            offenders.append(f"{test_file.name} reads {module}.__file__")

    assert not offenders, (
        f"{offenders} - scan the package instead (PACKAGE.rglob('*.py'))."
    )


def test_the_scan_catches_the_shapes_it_was_written_for():
    """A guard that passes proves nothing; fed the exact shapes it exists to
    catch, and the ones it has to let through."""
    went_blind = 'SOURCE = pathlib.Path(sensor.__file__).read_text(encoding="utf-8")'
    assert _tests_that_read_one_source_file(went_blind) == {"sensor"}

    package_wide = (
        "PACKAGE = pathlib.Path(sensor.__file__).parent\n"
        'sources = {f.name: f.read_text(encoding="utf-8") for f in PACKAGE.rglob("*.py")}'
    )
    assert _tests_that_read_one_source_file(package_wide) == set()

    by_package_path = 'source = (PACKAGE / "const.py").read_text(encoding="utf-8")'
    assert _tests_that_read_one_source_file(by_package_path) == {"const"}

    bound_first = (
        'CONST = pathlib.Path(__file__).resolve().parents[1] / "x" / "const.py"\n'
        "def test_it():\n"
        "    tree = ast.parse(CONST.read_text(encoding='utf-8'))\n"
    )
    assert _tests_that_read_one_source_file(bound_first) == {"const"}

    a_throwaway = 'text = (root / "module.py").read_text(encoding="utf-8")'
    assert _tests_that_read_one_source_file(a_throwaway) == set()


def test_every_guard_file_is_listed_and_present():
    """Deleting a guard is otherwise a green diff."""
    missing = [
        name
        for name in GUARD_FILES
        if not (TESTS / name).exists()
        or "def test_" not in (TESTS / name).read_text(encoding="utf-8")
    ]

    assert not missing, (
        f"guard file(s) gone or emptied: {missing}. If the protection is "
        "genuinely obsolete, remove the entry here in the same commit and "
        "say in the message what replaced it."
    )


def _scans_the_package(source: str) -> bool:
    """Whether `source` walks a whole directory tree for every module,
    however it spells the receiver.

    Asked of the CALL, not of the variable in front of it: matching the text
    "PACKAGE.rglob" would go blind the moment a guard binds its path to a
    different name. Both `glob` and `rglob` count - a non-recursive
    `glob("*.py")` still claims every file directly in the directory it is
    pointed at, and a future guard could write one against PACKAGE and miss
    the wago_879_api subpackage without either spelling being exempt by
    construction. The one legitimate exception (test_registers.py, which
    means to look at only two files) is named in PACKAGE_SCAN_EXEMPTIONS
    instead of being carved out of this detector.
    """
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("glob", "rglob")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            # Exactly "*.py" - every module. `test_*.py` is a scan of the test
            # directory, which this file does and which says nothing about
            # the package.
            and node.args[0].value == "*.py"
        ):
            return True
    return False


def test_the_index_scan_is_not_pinned_to_a_variable_name():
    assert _scans_the_package(
        'for module in sorted(package.rglob("*.py")):\n    pass\n'
    )
    assert _scans_the_package('PACKAGE.rglob("*.py")')
    assert not _scans_the_package('TESTS.glob("test_*.py")'), (
        "a walk of the test directory is not a scan of the package"
    )
    assert not _scans_the_package("PACKAGE.read_text()")
    assert _scans_the_package('api.glob("*.py")'), (
        "glob and rglob both count as scanning every module in a directory"
    )


def test_a_new_package_wide_scan_is_added_to_the_index():
    """A guard nobody lists is a guard nobody knows to keep."""
    scanning = {
        test_file.name
        for test_file in sorted(TESTS.glob("test_*.py"))
        if _scans_the_package(test_file.read_text(encoding="utf-8"))
    }

    unlisted = scanning - set(GUARD_FILES) - set(PACKAGE_SCAN_EXEMPTIONS)
    assert not unlisted, (
        f"{unlisted} scan(s) the package but are not in GUARD_FILES - add a "
        "line saying what each one holds, or in PACKAGE_SCAN_EXEMPTIONS with "
        "a reason if the scan is deliberately narrow."
    )


# What CI runs, and the text that proves each one is INVOKED - one needle per
# side, because the two files spell the same call differently: the workflow
# runs `mypy`, check.sh runs `"$PYTHON" -m mypy`. The needles have to be that
# precise on BOTH sides: check.sh prints a banner per gate, so `mypy` alone
# would match `echo "== mypy =="` with the call deleted, and the workflow names
# its jobs after their tool, so the same word matches `name: mypy` in a job
# that runs nothing.
TOOL_INVOCATIONS = {
    "ruff check": ("run: ruff check", "ruff check"),
    "ruff format --check": ("run: ruff format --check", "ruff format --check"),
    "mypy": ("run: mypy", "-m mypy"),
    "pip-audit": (
        "run: pip-audit -r requirements.txt",
        "-m pip_audit -r requirements.txt",
    ),
    "gitleaks": ("uses: gitleaks/gitleaks-action", "gitleaks git"),
    "pytest": ("run: pytest tests/", "-m pytest tests/"),
    "check_min_ha.py": (
        "run: python .github/scripts/check_min_ha.py",
        "check_min_ha.py",
    ),
    "mutate.py": ("mutate.py .github/mutations", "mutate.py .github/mutations"),
    "install_core_modbus_requirements.py": (
        "run: python .github/scripts/install_core_modbus_requirements.py",
        ".github/scripts/install_core_modbus_requirements.py",
    ),
}

WORKFLOW = REPO / ".github" / "workflows" / "test.yaml"
CHECK = REPO / ".github" / "scripts" / "check.sh"


def _tools_ci_runs_and_the_local_check_does_not(workflow: str, check: str) -> set:
    in_ci = {
        name
        for name, (in_workflow, _) in TOOL_INVOCATIONS.items()
        if in_workflow in workflow
    }
    return {name for name in in_ci if TOOL_INVOCATIONS[name][1] not in check}


def test_every_tool_is_recognised_on_the_ci_side_too():
    """A needle that no longer matches the workflow empties the comparison
    instead of failing it - the guard reports nothing missing because it is
    looking for nothing."""
    workflow = WORKFLOW.read_text(encoding="utf-8")

    unseen = [
        name
        for name, (in_workflow, _) in TOOL_INVOCATIONS.items()
        if in_workflow not in workflow
    ]

    assert not unseen, (
        f"{unseen} are no longer recognised in the workflow, so the check "
        "below silently stops asking about them."
    )


def test_the_local_check_runs_every_tool_ci_runs():
    """A tool added to CI and forgotten in check.sh turns the local run into a
    claim it cannot back."""
    missing_locally = _tools_ci_runs_and_the_local_check_does_not(
        WORKFLOW.read_text(encoding="utf-8"), CHECK.read_text(encoding="utf-8")
    )

    assert not missing_locally, (
        f"CI runs {missing_locally} but .github/scripts/check.sh does not. "
        "Add it there too, or the local run promises more than it checks."
    )


def test_the_comparison_is_not_satisfied_by_a_banner():
    """The shape it exists to catch: a check.sh that still ANNOUNCES the gate
    but no longer runs it."""
    banner_only = '#!/bin/sh\necho "== mypy =="\necho "== ruff =="\n'

    assert _tools_ci_runs_and_the_local_check_does_not(
        "        run: mypy\n", banner_only
    ) == {"mypy"}
