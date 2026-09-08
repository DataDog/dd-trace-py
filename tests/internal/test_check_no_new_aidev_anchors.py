"""Tests for scripts/check_no_new_aidev_anchors.py."""

import importlib.util
import pathlib
import types

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_no_new_aidev_anchors.py"
_SPEC: importlib.machinery.ModuleSpec | None = importlib.util.spec_from_file_location(
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
    ],
)
def test_is_anchor_line_detects_comment_forms(line: str) -> None:
    assert _MODULE._is_anchor_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "+ This prose mentions `AIDEV-NOTE:` but is not a comment.",
        '+ value = "/* AIDEV-NOTE: inside a string */"',
        '+ value = "# AIDEV-TODO: inside a string"',
        "+ https://example.test// AIDEV-QUESTION: URL text",
        "+ value * AIDEV-NOTE: multiplication expression",
    ],
)
def test_is_anchor_line_ignores_prose_and_strings(line: str) -> None:
    assert not _MODULE._is_anchor_line(line)
