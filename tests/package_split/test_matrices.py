"""Matrix file parsing: strict schema, required defaults, command normalization."""

from __future__ import annotations

from package_split.matrices import MatrixError
from package_split.matrices import parse_matrix
from package_split.matrices import scenario_commands
import pytest


COMPAT_MATRIX = """\
schema_version = 1

[[scenario]]
name = "minimum-deps"
installs = [{ artifact = "candidate" }, { requirement = "wrapt==1.13" }]

[scenario.expectations]
imports_ok = ["sample.thing"]
imports_fail = ["sample.native_feature"]
commands = ["import sample.thing"]

[[scenario]]
name = "broken-combo"
required = false
installs = [{ artifact = "candidate" }]
"""

MIGRATION_MATRIX = """\
schema_version = 1

[[scenario]]
name = "coexistence"

[[scenario.step]]
action = "install"
artifact = "candidate"

[[scenario.step]]
action = "uninstall"
distribution = "oldpkg"

[scenario.checks]
file_ownership = true
imports_ok = ["sample"]

[scenario.checks.identity]
bridge = "bridge:Provider"
expect_module = "sample.provider"

[scenario.checks.entry_points]
group = "pytest11"
count = 1
"""


def test_compatibility_matrix_parsing(tmp_path):
    path = tmp_path / "matrix.toml"
    path.write_text(COMPAT_MATRIX, encoding="utf-8")
    matrix = parse_matrix(path, "compatibility")
    assert matrix.kind == "compatibility"
    assert [s.name for s in matrix.scenarios] == ["minimum-deps", "broken-combo"]
    first = matrix.scenarios[0]
    assert first.required is True  # default
    assert first.installs[0] == {"artifact": "candidate"}
    assert first.installs[1] == {"requirement": "wrapt==1.13"}
    assert matrix.scenarios[1].required is False


def test_migration_matrix_parsing(tmp_path):
    path = tmp_path / "matrix.toml"
    path.write_text(MIGRATION_MATRIX, encoding="utf-8")
    matrix = parse_matrix(path, "migration")
    scenario = matrix.scenarios[0]
    assert [step.action for step in scenario.steps] == ["install", "uninstall"]
    assert scenario.steps[1].distribution == "oldpkg"
    assert scenario.checks["identity"]["bridge"] == "bridge:Provider"
    assert scenario.checks["entry_points"]["count"] == 1


@pytest.mark.parametrize(
    "kind,text,issue",
    [
        ("compatibility", "", "at least one"),
        ("compatibility", "schema_version = 99\n[[scenario]]\nname = 'x'\n", "schema_version"),
        ("compatibility", "schema_version = 1\n[[scenario]]\nrequired = true\n", "missing name"),
        (
            "compatibility",
            "schema_version = 1\n[[scenario]]\nname = 'x'\ninstalls = [{ artifact = 'a', requirement = 'b' }]\n",
            "exactly one of",
        ),
        (
            "migration",
            "schema_version = 1\n[[scenario]]\nname = 'x'\nsteps = [{ action = 'explode' }]\n",
            "install/uninstall",
        ),
        (
            "migration",
            "schema_version = 1\n[[scenario]]\nname = 'x'\nsteps = [{ action = 'uninstall' }]\n",
            "uninstall needs",
        ),
    ],
)
def test_invalid_matrices(tmp_path, kind, text, issue):
    path = tmp_path / "matrix.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(MatrixError) as excinfo:
        parse_matrix(path, kind)
    assert issue in str(excinfo.value)


def test_scenario_commands_normalization():
    assert scenario_commands(["import a", "import b"]) == [
        ["-c", "import a"],
        ["-c", "import b"],
    ]
    assert scenario_commands([["-m", "pytest", "-q"]]) == [["-m", "pytest", "-q"]]
    with pytest.raises(MatrixError):
        scenario_commands([42])
