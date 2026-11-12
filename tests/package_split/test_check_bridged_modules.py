"""check_bridged_modules: the completeness gate for modules bridged to extracted packages."""

from __future__ import annotations

from pathlib import Path
import sys

from check_bridged_modules import check
from check_bridged_modules import discover_bridges
from check_bridged_modules import parse_pyproject
import pytest


PYPROJECT = """\
[project]
dependencies = [
    "bytecode>=0.13.0,<1",
    "ddtrace-internal-base>=0.1.0",
]

[dependency-groups]
lint = [
    "envier==0.6.1",
    "ddtrace-internal-base==0.1.0",
]
"""

BRIDGE = '''"""Bridge to the extracted fake package."""

from _ddtrace_internal_base.fake import *  # noqa: F401,F403 -- re-export
from _ddtrace_internal_base.fake import Fake as Fake  # noqa: F401 -- re-export for type checkers
'''

CONSUMER = """\
from ddtrace.internal.fake import Fake

USE = Fake
"""

NON_BRIDGE_IMPORT = """\
from _ddtrace_internal.modules import check_module_path

def patch():
    return check_module_path
"""

IDENTITY_TEST = """\
from ddtrace.internal.fake import Fake


def test_bridge_resolves_to_extracted_package():
    from _ddtrace_internal_base.fake import Fake as ExtractedFake

    assert Fake is ExtractedFake
"""

STUB_PACKAGE = """\
class Fake:
    pass
"""

REQUIREMENTS_CSV = "Dependency,Version Specifier,Python Version\nddtrace-internal-base,>=0.1.0,\n"


@pytest.fixture(autouse=True)
def _clean_extracted_modules():
    """The stub extracted package must win over the real one installed in the test env.

    ddtrace itself imports the real ``_ddtrace_internal_base`` at startup (the
    codeowners bridge), so it is always cached in sys.modules when these tests
    run. Drop it -- and any leftovers from sibling tests -- before and after
    each test so only the fixture stub is importable.
    """

    def _purge():
        for name in [name for name in sys.modules if name.startswith("_ddtrace_internal")]:
            del sys.modules[name]

    path_before = sys.path[:]
    _purge()
    yield
    sys.path[:] = path_before
    _purge()


def build_repo(
    tmp_path: Path,
    *,
    runtime_dep: bool = True,
    lint_pin: bool = True,
    requirements_row: bool = True,
    identity_test: bool = True,
    consumer: str = CONSUMER,
    stub_names: tuple[str, ...] = ("Fake",),
) -> Path:
    """A minimal repo with one bridged module and its extracted stub package."""
    pyproject = PYPROJECT
    if not runtime_dep:
        pyproject = pyproject.replace('    "ddtrace-internal-base>=0.1.0",\n', "")
    if not lint_pin:
        pyproject = pyproject.replace('    "ddtrace-internal-base==0.1.0",\n', "")

    requirements = REQUIREMENTS_CSV if requirements_row else "Dependency,Version Specifier,Python Version\n"

    stub = ",\n".join("%s = object()" % name for name in stub_names)
    files = {
        "pyproject.toml": pyproject,
        "requirements.csv": requirements,
        "lib-injection/sources/requirements.csv": requirements,
        "ddtrace/internal/fake.py": BRIDGE,
        "ddtrace/internal/__init__.py": "",
        "ddtrace/internal/consumer.py": consumer,
        "ddtrace/contrib/internal/patch.py": NON_BRIDGE_IMPORT,
        "tests/internal/__init__.py": "",
    }
    if identity_test:
        files["tests/internal/test_fake.py"] = IDENTITY_TEST
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    stubs = tmp_path / "stubs"
    (stubs / "_ddtrace_internal_base").mkdir(parents=True)
    (stubs / "_ddtrace_internal_base" / "__init__.py").write_text("", encoding="utf-8")
    (stubs / "_ddtrace_internal_base" / "fake.py").write_text(stub + "\n", encoding="utf-8")
    sys.path.insert(0, str(stubs))
    return tmp_path


def test_complete_bridge_passes(tmp_path):
    root = build_repo(tmp_path)
    bridges, errors = check(root)
    assert len(bridges) == 1
    assert bridges[0].module == "ddtrace.internal.fake"
    assert errors == []


def test_real_repo_pyproject_parses():
    """The text parser handles the real pyproject.toml (markers, multiple same-name entries)."""
    root = Path(__file__).resolve().parents[2]
    runtime, lint = parse_pyproject((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "bytecode" in runtime
    assert "wrapt" in runtime
    assert lint["envier"] == "==0.6.1"


def test_missing_runtime_dependency_reported(tmp_path):
    root = build_repo(tmp_path, runtime_dep=False)
    _, errors = check(root)
    assert any("missing from [project.dependencies]" in error for error in errors)


def test_missing_lint_pin_reported(tmp_path):
    root = build_repo(tmp_path, lint_pin=False)
    _, errors = check(root)
    assert any("missing from the lint dependency group" in error for error in errors)


def test_unpinned_lint_entry_reported(tmp_path):
    root = build_repo(tmp_path)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject = pyproject.replace("ddtrace-internal-base==0.1.0", "ddtrace-internal-base>=0.1.0")
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    _, errors = check(root)
    assert any("must pin" in error for error in errors)


def test_pin_below_runtime_bound_reported(tmp_path):
    root = build_repo(tmp_path)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject = pyproject.replace("ddtrace-internal-base>=0.1.0", "ddtrace-internal-base>=0.2.0")
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    requirements = REQUIREMENTS_CSV.replace(">=0.1.0", ">=0.2.0")
    (root / "requirements.csv").write_text(requirements, encoding="utf-8")
    (root / "lib-injection/sources/requirements.csv").write_text(requirements, encoding="utf-8")
    _, errors = check(root)
    assert any("below the runtime minimum" in error for error in errors)


def test_missing_requirements_row_reported(tmp_path):
    root = build_repo(tmp_path, requirements_row=False)
    _, errors = check(root)
    assert any("missing from requirements.csv" in error for error in errors)
    assert any("missing from lib-injection/sources/requirements.csv" in error for error in errors)


def test_stale_requirements_row_reported(tmp_path):
    root = build_repo(tmp_path)
    requirements = REQUIREMENTS_CSV.replace(">=0.1.0", ">=0.2.0")
    (root / "requirements.csv").write_text(requirements, encoding="utf-8")
    _, errors = check(root)
    assert any("out of date in requirements.csv" in error for error in errors)


def test_missing_identity_test_reported(tmp_path):
    root = build_repo(tmp_path, identity_test=False)
    _, errors = check(root)
    assert any("no test imports both the bridge" in error for error in errors)


def test_reexport_missing_from_extracted_reported(tmp_path):
    """A bridge re-exporting a name the extracted package lacks fails the gate."""
    root = build_repo(tmp_path)
    bridged = BRIDGE + "from _ddtrace_internal_base.fake import Gone as Gone  # noqa: F401\n"
    (root / "ddtrace/internal/fake.py").write_text(bridged, encoding="utf-8")
    _, errors = check(root)
    assert any("re-exported name Gone missing" in error for error in errors)


def test_consumer_name_missing_from_extracted_reported(tmp_path):
    root = build_repo(tmp_path, consumer="from ddtrace.internal.fake import Ghost\n")
    _, errors = check(root)
    assert any("imports Ghost" in error for error in errors)


def test_non_bridge_import_is_not_a_bridge(tmp_path):
    """Modules with real code that import _ddtrace_internal are not bridges."""
    root = build_repo(tmp_path, consumer="")
    bridges = discover_bridges(root)
    assert [bridge.module for bridge in bridges] == ["ddtrace.internal.fake"]


def test_no_bridges_is_clean(tmp_path):
    root = build_repo(tmp_path, consumer="")
    (root / "ddtrace/internal/fake.py").unlink()
    bridges, errors = check(root)
    assert bridges == []
    assert errors == []
