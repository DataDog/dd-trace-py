"""Tests for the native ddtrace-import checker."""

from __future__ import annotations

import importlib.util
import pathlib
import types
from typing import Optional

import pytest


_SCRIPT_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_native_python_imports.py"
_SPEC: Optional[importlib.machinery.ModuleSpec] = importlib.util.spec_from_file_location(
    "check_native_python_imports", _SCRIPT_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE: types.ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


@pytest.fixture
def native_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "src" / "native"
    root.mkdir(parents=True)
    return root


def _write(root: pathlib.Path, name: str, source: str) -> None:
    (root / name).write_text(source, encoding="utf-8")


def test_flags_every_import_call_form(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "real.rs",
        "\n".join(
            [
                'let ok = py.import("json")?;',
                'py.import("ddtrace.internal.logger")?;',
                'PyModule::import(m.py(), "ddtrace.internal.logger")?;',
                'self_.import(intern!(py, "ddtrace.other"));',
                'import_bound(py, "ddtrace._trace.span");',
            ]
        ),
    )

    violations = _MODULE.find_violations(native_root)

    assert violations == [
        "real.rs:2: imports a ddtrace module",
        "real.rs:3: imports a ddtrace module",
        "real.rs:4: imports a ddtrace module",
        "real.rs:5: imports a ddtrace module",
    ]


def test_ignores_sibling_and_allowed_imports(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "ok.rs",
        "\n".join(
            [
                'let sibling = py.import("_msgpack")?;',
                'let allowed = py.import("ddtrace.internal._encoding")?;',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == []


def test_ignores_plain_string_literals(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "strings.rs",
        "\n".join(
            [
                '#[pyclass(module = "ddtrace.internal._native")]',
                "struct Extension;",
                'let event = "ddtrace.event.name";',
                'let esc = "import(\\"ddtrace.z\\")";',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == []


def test_raw_string_content_cannot_trigger_the_check(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "raw.rs",
        "\n".join(
            [
                'const EXAMPLE: &str = r#"py.import("ddtrace.internal.logger")"#;',
                'let byte = br#"PyModule::import(py, "ddtrace.x")"#;',
                'let also = r##"import_bound("ddtrace.y")"##;',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == []


def test_flags_import_after_string_containing_double_slash(native_root: pathlib.Path) -> None:
    # Comment stripping must not start inside a string literal: the // in the URL would swallow the
    # rest of the line and hide the real import below.
    _write(
        native_root,
        "url.rs",
        "\n".join(
            [
                'let url = "https://example.test";',
                'py.import("ddtrace.internal.logger")?;',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == ["url.rs:2: imports a ddtrace module"]


def test_ignores_line_and_nested_block_comments(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "comments.rs",
        "\n".join(
            [
                '// py.import("ddtrace.a")',
                '/* PyModule::import(py, "ddtrace.b") */',
                '/* outer /* py.import("ddtrace.c") */ still a comment */',
                'let x = 1; // trailing: import_bound("ddtrace.d")',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == []


def test_char_literals_with_paren_or_quote_do_not_confuse_the_scan(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "chars.rs",
        "\n".join(
            [
                "let paren = ')';",
                "let quote = '\"';",
                "let lifetime = &'static str;",
                'py.import("ddtrace.internal.logger")?;',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == ["chars.rs:4: imports a ddtrace module"]


def test_flags_import_whose_earlier_argument_has_a_closing_paren(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "nested.rs",
        "\n".join(
            [
                'PyModule::import(m.py(), "ddtrace.internal.logger")?;',
                'py.import(format!("{}", x), "ddtrace.other")?;',
            ]
        ),
    )

    assert _MODULE.find_violations(native_root) == [
        "nested.rs:1: imports a ddtrace module",
        "nested.rs:2: imports a ddtrace module",
    ]


def test_flags_import_despite_escaped_quote_in_an_earlier_argument(native_root: pathlib.Path) -> None:
    _write(
        native_root,
        "escape.rs",
        'py.import(name.unwrap_or("\\")"), "ddtrace.internal.logger")?;\n',
    )

    assert _MODULE.find_violations(native_root) == ["escape.rs:1: imports a ddtrace module"]


def test_masks_preserve_length_and_newlines() -> None:
    source = "\n".join(
        [
            'let s = r#"py.import("ddtrace.x")"#;',
            "// comment",
            'let u = "https://x.test";',
            "let c = ')';",
        ]
    )
    code_view, args_view = _MODULE._masked_views(source)

    assert len(code_view) == len(source) == len(args_view)
    assert code_view.count("\n") == source.count("\n") == args_view.count("\n")
    # Line numbers come from counting newlines before a match, so they must be identical in every
    # view at the same offset.
    for a, b, c in zip(source, code_view, args_view):
        assert (a == "\n") == (b == "\n") == (c == "\n")
