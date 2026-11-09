"""Tests for the rST-markup-in-non-rendered-prose checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Optional
from typing import Union

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_no_rst_markup_in_prose.py"
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
        "See ``finish\n()`` now.",
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
        'print("```")',
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


def _install_diff(
    monkeypatch: pytest.MonkeyPatch,
    diff: Union[str, bytes],
    source: Optional[str] = None,
) -> None:
    payload: bytes = diff.encode("utf-8") if isinstance(diff, str) else diff

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
    ) -> types.SimpleNamespace:
        return types.SimpleNamespace(stdout=payload, stderr=b"")

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)
    if source is not None:
        monkeypatch.setattr(_MODULE, "_read_source", lambda path: source)


def test_added_lines_flags_new_docstring_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    source: str = 'def example():\n    """Covers the ``finish()`` contract."""\n    unchanged = True\n'
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_span.py b/tests/tracer/test_span.py",
            "--- a/tests/tracer/test_span.py",
            "+++ b/tests/tracer/test_span.py",
            "@@ -1,0 +2,2 @@",
            '+    """Covers the ``finish()`` contract."""',
            "+    unchanged = True",
        ]
    )
    _install_diff(monkeypatch, diff, source)

    violations: list[tuple[str, int, str]] = _MODULE._added_lines("origin/main")

    assert violations == [
        ("tests/tracer/test_span.py", 2, '    """Covers the ``finish()`` contract."""'),
    ]


def test_added_lines_flags_markup_wrapped_across_docstring_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    source: str = 'def example():\n    """See ``finish\n    ()`` now.\n    """\n    return 1\n'
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_span.py b/tests/tracer/test_span.py",
            "--- a/tests/tracer/test_span.py",
            "+++ b/tests/tracer/test_span.py",
            "@@ -1,0 +2,2 @@",
            '+    """See ``finish',
            "+    ()`` now.",
        ]
    )
    _install_diff(monkeypatch, diff, source)

    violations: list[tuple[str, int, str]] = _MODULE._added_lines("origin/main")

    assert violations == [
        ("tests/tracer/test_span.py", 2, '    """See ``finish'),
        ("tests/tracer/test_span.py", 3, "    ()`` now."),
    ]


def test_added_lines_ignores_executable_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    source: str = 'def example():\n    assert msg == "Use ``ddtrace.trace.tracer`` instead."\n'
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_tracer.py b/tests/tracer/test_tracer.py",
            "--- a/tests/tracer/test_tracer.py",
            "+++ b/tests/tracer/test_tracer.py",
            "@@ -1,0 +2 @@",
            '+    assert msg == "Use ``ddtrace.trace.tracer`` instead."',
        ]
    )
    _install_diff(monkeypatch, diff, source)

    assert _MODULE._added_lines("origin/main") == []


def test_added_lines_flags_comment_but_not_code_on_the_same_line(monkeypatch: pytest.MonkeyPatch) -> None:
    source: str = 'x = "Use ddtrace.trace.tracer instead."  # see ``tracer``\n'
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_tracer.py b/tests/tracer/test_tracer.py",
            "--- a/tests/tracer/test_tracer.py",
            "+++ b/tests/tracer/test_tracer.py",
            "@@ -0,0 +1 @@",
            '+x = "Use ddtrace.trace.tracer instead."  # see ``tracer``',
        ]
    )
    _install_diff(monkeypatch, diff, source)

    violations: list[tuple[str, int, str]] = _MODULE._added_lines("origin/main")

    assert violations == [
        ("tests/tracer/test_tracer.py", 1, 'x = "Use ddtrace.trace.tracer instead."  # see ``tracer``'),
    ]


def test_added_lines_ignores_code_backticks_beside_a_plain_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    source: str = 'x = "Use ``ddtrace.trace.tracer`` instead."  # plain\n'
    diff: str = "\n".join(
        [
            "diff --git a/tests/tracer/test_tracer.py b/tests/tracer/test_tracer.py",
            "--- a/tests/tracer/test_tracer.py",
            "+++ b/tests/tracer/test_tracer.py",
            "@@ -0,0 +1 @@",
            '+x = "Use ``ddtrace.trace.tracer`` instead."  # plain',
        ]
    )
    _install_diff(monkeypatch, diff, source)

    assert _MODULE._added_lines("origin/main") == []


def test_added_lines_keeps_source_that_begins_with_plus_signs(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/tests/fixture.txt b/tests/fixture.txt",
            "--- a/tests/fixture.txt",
            "+++ b/tests/fixture.txt",
            "@@ -0,0 +1 @@",
            "+++ ``forbidden``",
        ]
    )
    _install_diff(monkeypatch, diff)

    violations: list[tuple[str, int, str]] = _MODULE._added_lines("origin/main")

    assert violations == [("tests/fixture.txt", 1, "++ ``forbidden``")]


def test_added_lines_unquotes_git_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            'diff --git "a/tests/t\\303\\251st.txt" "b/tests/t\\303\\251st.txt"',
            "--- /dev/null",
            '+++ "b/tests/t\\303\\251st.txt"',
            "@@ -0,0 +1 @@",
            "+# ``quoted``",
        ]
    )
    _install_diff(monkeypatch, diff)

    violations: list[tuple[str, int, str]] = _MODULE._added_lines("origin/main")

    assert violations == [("tests/tést.txt", 1, "# ``quoted``")]


def test_added_lines_decodes_non_utf8_diff_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    text: str = "\n".join(
        [
            "diff --git a/tests/fixture.txt b/tests/fixture.txt",
            "--- a/tests/fixture.txt",
            "+++ b/tests/fixture.txt",
            "@@ -1 +1,2 @@",
            "+# note ``keep``",
        ]
    )
    _install_diff(monkeypatch, (text + "\n").encode("utf-8") + b"+\xff\n")

    violations: list[tuple[str, int, str]] = _MODULE._added_lines("origin/main")

    assert ("tests/fixture.txt", 1, "# note ``keep``") in violations


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
    _install_diff(monkeypatch, diff)

    assert _MODULE._added_lines("origin/main") == []


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
    _install_diff(monkeypatch, diff, "unchanged = True\n")

    assert _MODULE._added_lines("origin/main") == []


def test_added_lines_skips_excluded_checker_and_test_files(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/scripts/check_no_rst_markup_in_prose.py b/scripts/check_no_rst_markup_in_prose.py",
            "--- a/scripts/check_no_rst_markup_in_prose.py",
            "+++ b/scripts/check_no_rst_markup_in_prose.py",
            "@@ -1 +1,2 @@",
            "+# Example of a banned ``pattern`` kept here as documentation.",
            "+unchanged",
        ]
    )
    _install_diff(monkeypatch, diff)

    assert _MODULE._added_lines("origin/main") == []
