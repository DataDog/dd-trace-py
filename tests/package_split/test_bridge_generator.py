"""package_split.bridge: the generator for bridge-module scaffolds."""

from __future__ import annotations

import ast
from pathlib import Path

from package_split.bridge import bridge_source
from package_split.bridge import bridge_test_source
from package_split.bridge import generate
from package_split.bridge import insert_dependency
from package_split.bridge import public_names
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

VENDORED = """\
import re


def helper():
    return 1


class Widget:
    pass


_PRIVATE = 1
"""


def test_public_names_skips_underscores():
    tree = ast.parse(VENDORED)
    assert public_names(tree) == ["helper", "Widget"]


def test_bridge_source_uses_alias_reexports():
    source = bridge_source("_ddtrace_internal_base.widget", ["Widget", "helper"])
    assert "from _ddtrace_internal_base.widget import *  # noqa: F401,F403 -- re-export" in source
    assert "from _ddtrace_internal_base.widget import Widget as Widget  # noqa: F401 -- re-export" in source
    ast.parse(source)


def test_insert_dependency_sorts_into_real_pyproject():
    """Insertion keeps the real pyproject.toml ordered and comment-preserving."""
    original = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    result = insert_dependency(original, "project", "dependencies = [", "zzz-test-distribution>=1.2.3")
    assert '"zzz-test-distribution>=1.2.3",' in result
    # Inserted in name order: after wrapt, before the end of the dependencies list.
    assert (
        result.index('"wrapt')
        < result.index('"zzz-test-distribution')
        < result.index("[project.optional-dependencies]")
    )
    # Everything else is untouched: removal of the new line restores the file exactly.
    assert result.replace('    "zzz-test-distribution>=1.2.3",\n', "") == original


def test_insert_dependency_into_real_lint_group():
    original = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    result = insert_dependency(original, "dependency-groups", "lint = [", "aaa-test-distribution==1.2.3")
    assert '"aaa-test-distribution==1.2.3",' in result
    assert (
        result.index("[dependency-groups]")
        < result.index('"aaa-test-distribution')
        < result.index("[tool.cython-lint]")
    )
    assert result.replace('    "aaa-test-distribution==1.2.3",\n', "") == original


def test_insert_dependency_missing_section_raises():
    with pytest.raises(ValueError):
        insert_dependency("[project]\ndependencies = [\n]\n", "dependency-groups", "lint = [", "x==1")


def test_bridge_test_source_round_trips():
    source = bridge_test_source("ddtrace.internal.widget", "_ddtrace_internal_base.widget", "Widget")
    tree = ast.parse(source)
    assert "from ddtrace.internal.widget import Widget" in source
    assert "from _ddtrace_internal_base.widget import Widget as ExtractedWidget" in source
    assert len([node for node in tree.body if isinstance(node, ast.FunctionDef)]) == 1


def test_generate_writes_full_scaffold(tmp_path):
    root = tmp_path
    module = root / "ddtrace/internal/widget.py"
    module.parent.mkdir(parents=True)
    module.write_text(VENDORED, encoding="utf-8")
    pyproject_text = (
        '[project]\ndependencies = [\n    "envier~=0.6.1",\n]\n\n'
        '[dependency-groups]\nlint = [\n    "envier==0.6.1",\n]\n'
    )
    (root / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")

    commands = []

    def fake_run(command):
        commands.append(list(command))
        return 0

    plan = generate(
        module_path=module,
        distribution="ddtrace-internal-base",
        source="_ddtrace_internal_base.widget",
        version="0.1.0",
        root=root,
        run_command=fake_run,
    )

    assert module.read_text(encoding="utf-8") == bridge_source("_ddtrace_internal_base.widget", ["helper", "Widget"])
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"ddtrace-internal-base>=0.1.0",' in pyproject
    assert '"ddtrace-internal-base==0.1.0",' in pyproject
    test = plan.test.read_text(encoding="utf-8")
    assert "from ddtrace.internal.widget import helper" in test
    assert "from _ddtrace_internal_base.widget import helper as Extractedhelper" in test
    reno = plan.reno.read_text(encoding="utf-8")
    assert "``ddtrace-internal-base``" in reno
    assert plan.reno.parent == root / "releasenotes" / "notes"
    assert any("requirements_to_csv" in " ".join(command) for command in commands)


def test_generate_appends_to_existing_test(tmp_path):
    root = tmp_path
    module = root / "ddtrace/internal/widget.py"
    module.parent.mkdir(parents=True)
    module.write_text(VENDORED, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\ndependencies = [\n]\n\n[dependency-groups]\nlint = [\n]\n",
        encoding="utf-8",
    )
    test = root / "tests/internal/test_widget.py"
    test.parent.mkdir(parents=True)
    test.write_text("def test_existing():\n    pass\n", encoding="utf-8")

    generate(
        module_path=module,
        distribution="ddtrace-internal-base",
        source="_ddtrace_internal_base.widget",
        version="0.1.0",
        root=root,
        run_command=lambda command: 0,
    )
    content = test.read_text(encoding="utf-8")
    assert "def test_existing" in content
    assert "test_bridge_resolves_to_extracted_package" in content
