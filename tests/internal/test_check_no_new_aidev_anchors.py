"""Tests for the deprecated-anchor checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Optional

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_no_new_aidev_anchors.py"
_SPEC: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
    "check_no_new_aidev_anchors", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE: types.ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.mark.parametrize(
    "line",
    [
        "+ # AIDEV-NOTE: Python comment",
        "+ value # AIDEV-TODO: inline Python comment",
        "+ // AIDEV-QUESTION: C++ comment",
        "+ /* AIDEV-NOTE: block comment */",
        "+ * AIDEV-TODO: block continuation",
        "+ AIDEV-QUESTION: block continuation */",
        "+ AIDEV-NOTE: multiline docstring anchor",
        "+ # AIDEV-CONTEXT: future anchor type",
    ],
)
def test_is_anchor_line_detects_comment_forms(line: str) -> None:
    assert _MODULE._is_anchor_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "+ This prose mentions `AIDEV-NOTE:` but is not a comment.",
        '+ value = "/* AIDE" "V-NOTE: inside a string */"',
        '+ value = "# AIDE" "V-TODO: inside a string"',
    ],
)
def test_is_anchor_line_ignores_strings(line: str) -> None:
    assert not _MODULE._is_anchor_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "+ https://example.test// AIDEV-QUESTION: URL text",
        "+ value * AIDEV-NOTE: multiplication expression",
    ],
)
def test_is_anchor_line_detects_any_unquoted_occurrence(line: str) -> None:
    assert _MODULE._is_anchor_line(line)


@pytest.mark.parametrize(
    "line",
    [
        '+ """AIDE' "V-NOTE: docstring opening line" '"""',
        "+     AIDE" "V-TODO: multiline docstring line",
    ],
)
def test_is_anchor_line_detects_docstrings(line: str) -> None:
    assert _MODULE._is_anchor_line(line)


def test_added_lines_ignores_deleted_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    diff: str = "\n".join(
        [
            "diff --git a/example.py b/example.py",
            "--- a/example.py",
            "+++ b/example.py",
            "@@ -1,2 +1,1 @@",
            "-# AIDEV-NOTE: legacy anchor",
            " unchanged",
        ]
    )

    def fake_merge_base(base_ref: str) -> str:
        return "base"

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> types.SimpleNamespace:
        return types.SimpleNamespace(stdout=diff)

    monkeypatch.setattr(_MODULE, "_merge_base", fake_merge_base)
    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)

    violations: list[tuple[str, str]] = _MODULE._added_lines("origin/main")

    assert violations == []
