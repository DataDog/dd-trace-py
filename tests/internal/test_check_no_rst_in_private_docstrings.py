"""Tests for the private-docstring rST markup checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Optional

import pytest


_SCRIPT_PATH: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_no_rst_in_private_docstrings.py"
)
_SPEC: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
    "check_no_rst_in_private_docstrings", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE: types.ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_rendered_modules_includes_known_automodule_targets() -> None:
    # Run from the real repository checkout, where docs/*.rst exists, so this
    # exercises the actual `.. automodule::` directives rather than a fixture.
    rendered = _MODULE._rendered_modules()
    assert "ddtrace.contrib.bottle" in rendered
    assert "ddtrace.debugging" in rendered


def test_is_rendered_true_for_automodule_target() -> None:
    rendered = frozenset({"ddtrace.contrib.bottle"})
    assert _MODULE._is_rendered("ddtrace/contrib/bottle.py", rendered)


def test_is_rendered_false_for_private_module() -> None:
    rendered = frozenset({"ddtrace.contrib.bottle"})
    assert not _MODULE._is_rendered("ddtrace/internal/_threads.py", rendered)


def test_is_rendered_false_for_pyi_stub_even_if_module_matches() -> None:
    # .pyi stubs are never Sphinx-rendered, regardless of the module name.
    rendered = frozenset({"ddtrace.contrib.bottle"})
    assert not _MODULE._is_rendered("ddtrace/contrib/bottle.pyi", rendered)


def test_double_backtick_regex_matches_simple_literal() -> None:
    assert _MODULE.DOUBLE_BACKTICK_RE.search("Calls ``foo()`` here.")


def test_double_backtick_regex_ignores_markdown_fences() -> None:
    # Three or more backticks (Markdown code fences) should not match the
    # two-backtick rST literal pattern.
    assert not _MODULE.DOUBLE_BACKTICK_RE.search("```python\ncode\n```")


def test_doc_and_comment_spans_finds_docstring_and_comment() -> None:
    source = '''def f():
    """Uses ``x`` in a docstring."""
    # Uses ``y`` in a comment.
    return 1
'''
    spans = _MODULE._doc_and_comment_spans(source)
    texts = [text for _, _, text in spans]
    assert any("``x``" in t for t in texts)
    assert any("``y``" in t for t in texts)


def test_violations_in_file_only_flags_added_lines(tmp_path: pathlib.Path) -> None:
    source = '''def f():
    """Uses ``x`` on line 2, pre-existing."""
    # Uses ``y`` on line 3, newly added.
    return 1
'''
    target = tmp_path / "_scratch.py"
    target.write_text(source)

    # Monkeypatch REPO_ROOT so _violations_in_file resolves relative to tmp_path.
    original_root = _MODULE.REPO_ROOT
    _MODULE.REPO_ROOT = tmp_path
    try:
        # Only line 3 (the comment) was added; line 2's docstring predates the diff.
        hits = _MODULE._violations_in_file("_scratch.py", added_lines={3})
    finally:
        _MODULE.REPO_ROOT = original_root

    assert hits == [(3, "``y``")]


@pytest.mark.parametrize(
    "path",
    [
        "scripts/check_no_rst_in_private_docstrings.py",
        "tests/internal/test_check_no_rst_in_private_docstrings.py",
    ],
)
def test_excluded_paths_are_skipped(path: str) -> None:
    assert _MODULE._is_excluded_path(path)
