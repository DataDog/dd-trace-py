"""Tests for the rST-markup-in-non-rendered-prose checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Optional

import pytest


_SCRIPT_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_no_rst_markup_in_prose.py"
)
_SPEC: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
    "check_no_rst_markup_in_prose", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE: types.ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.parametrize(
    "content",
    [
        "This mentions ``load_ai_guard()`` in prose.",
        "# ``patch()`` is called eagerly",
        '"""Docstring opener with ``inline`` markup.',
        "it's a ``test`` of apostrophes",
    ],
)
def test_has_rst_markup_detects_double_backticks(content: str) -> None:
    assert _MODULE._has_rst_markup(content)


@pytest.mark.parametrize(
    "content",
    [
        "This is plain prose with no markup at all.",
        "Uses a single `backtick` only, not doubled.",
        '    "> ```python code blocks, execute them"',
        "print(\"```\")",
        "````four backticks````",
    ],
)
def test_has_rst_markup_ignores_non_double_backtick_forms(content: str) -> None:
    assert not _MODULE._has_rst_markup(content)


@pytest.mark.parametrize(
    "path",
    [
        "tests/tracer/test_span.py",
        "tests/internal/test_module.py",
        "scripts/run-tests",
        "scripts/supported_configurations.py",
    ],
)
def test_is_in_scope_path_covers_tests_and_scripts(path: str) -> None:
    assert _MODULE._is_in_scope_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "ddtrace/internal/packages.py",
        "docs/api.rst",
        "AGENTS.md",
        "scripts/check_no_rst_markup_in_prose.py",
        "tests/internal/test_check_no_rst_markup_in_prose.py",
    ],
)
def test_is_in_scope_path_excludes_production_code_and_self(path: str) -> None:
    assert not _MODULE._is_in_scope_path(path)


def _fake_run_factory(diff: str):
    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> types.SimpleNamespace:
        return types.SimpleNamespace(stdout=diff)

    return fake_run


def test_added_lines_flags_new_markup_under_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_span.py b/tests/tracer/test_span.py",
            "--- a/tests/tracer/test_span.py",
            "+++ b/tests/tracer/test_span.py",
            "@@ -1 +1,2 @@",
            '+    """Covers the ``finish()`` contract."""',
            "+unchanged",
        ]
    )

    monkeypatch.setattr(_MODULE, "_merge_base", lambda base_ref: "base")
    monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run_factory(diff))

    violations: list[tuple[str, str]] = _MODULE._added_lines("origin/main")

    assert violations == [("tests/tracer/test_span.py", '    """Covers the ``finish()`` contract."""')]


def test_added_lines_ignores_production_code_path(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/ddtrace/internal/packages.py b/ddtrace/internal/packages.py",
            "--- a/ddtrace/internal/packages.py",
            "+++ b/ddtrace/internal/packages.py",
            "@@ -1 +1,2 @@",
            '+    """Uses ``this_is_fine`` because this module may be Sphinx-rendered."""',
            "+unchanged",
        ]
    )

    monkeypatch.setattr(_MODULE, "_merge_base", lambda base_ref: "base")
    monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run_factory(diff))

    violations: list[tuple[str, str]] = _MODULE._added_lines("origin/main")

    assert violations == []


def test_added_lines_ignores_deleted_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_span.py b/tests/tracer/test_span.py",
            "--- a/tests/tracer/test_span.py",
            "+++ b/tests/tracer/test_span.py",
            "@@ -1,2 +1,1 @@",
            '-    """Covers the ``finish()`` contract."""',
            " unchanged",
        ]
    )

    monkeypatch.setattr(_MODULE, "_merge_base", lambda base_ref: "base")
    monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run_factory(diff))

    violations: list[tuple[str, str]] = _MODULE._added_lines("origin/main")

    assert violations == []


def test_added_lines_ignores_markdown_code_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/scripts/import-analysis/cycles.py b/scripts/import-analysis/cycles.py",
            "--- a/scripts/import-analysis/cycles.py",
            "+++ b/scripts/import-analysis/cycles.py",
            "@@ -1 +1,2 @@",
            '+    "> ```\\n"',
            "+unchanged",
        ]
    )

    monkeypatch.setattr(_MODULE, "_merge_base", lambda base_ref: "base")
    monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run_factory(diff))

    violations: list[tuple[str, str]] = _MODULE._added_lines("origin/main")

    assert violations == []


def test_added_lines_skips_excluded_checker_and_test_files(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/scripts/check_no_rst_markup_in_prose.py b/scripts/check_no_rst_markup_in_prose.py",
            "--- a/scripts/check_no_rst_markup_in_prose.py",
            "+++ b/scripts/check_no_rst_markup_in_prose.py",
            "@@ -1 +1,2 @@",
            '+# Example of a banned ``pattern`` kept here as documentation.',
            "+unchanged",
        ]
    )

    monkeypatch.setattr(_MODULE, "_merge_base", lambda base_ref: "base")
    monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run_factory(diff))

    violations: list[tuple[str, str]] = _MODULE._added_lines("origin/main")

    assert violations == []
