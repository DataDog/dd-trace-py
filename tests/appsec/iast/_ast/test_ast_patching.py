#!/usr/bin/env python3
import logging
import os
from unittest import mock

import astunparse
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._ast.ast_patching import _in_python_stdlib
from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
from ddtrace.appsec._iast._ast.ast_patching import _trie_has_prefix_for
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from ddtrace.appsec._iast._ast.ast_patching import build_trie
from ddtrace.appsec._iast._ast.ast_patching import visit_ast
from ddtrace.internal.utils.formats import asbool
from tests.utils import override_env


_PREFIX = IAST.PATCH_ADDED_SYMBOL_PREFIX


@pytest.fixture(autouse=True, scope="module")
def clear_iast_env_vars():
    if IAST.PATCH_MODULES in os.environ:
        os.environ.pop("_DD_IAST_PATCH_MODULES")
    if IAST.DENY_MODULES in os.environ:
        os.environ.pop("_DD_IAST_DENY_MODULES")
    yield


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print('hi')", "test.py", "test"),
        ("print('str')", "test.py", "test"),
        ("str", "test.py", "test"),
    ],
)
def test_visit_ast_unchanged(source_text, module_path, module_name):
    """
    Source texts not containing:
    - str() calls
    - [...]  // To be filled with more aspects
    won't be modified by ast patching, so will return empty string
    """
    assert visit_ast(source_text, module_path, module_name) is None


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print(str('hi'))", "test.py", "test"),
        ("print(str('hi' + 'bye'))", "test.py", "test"),
        ("print('hi' + 'bye')", "test.py", "test"),
    ],
)
def test_visit_ast_changed(source_text, module_path, module_name):
    """
    Source texts containing:
    - str() calls
    - [...]  // To be filled with more aspects
    will be modified by ast patching, so will not return empty string
    """
    assert visit_ast(source_text, module_path, module_name) is not None


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.str.class_str"),
        ("tests.appsec.iast.fixtures.ast.str.function_str"),
    ],
)
def test_astpatch_module_changed(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) != (module_path, new_ast)
    new_code = astunparse.unparse(new_ast)
    assert new_code.startswith(
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.add_operator.basic"),
    ],
)
def test_astpatch_module_changed_add_operator(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) != (module_path, new_ast)
    new_code = astunparse.unparse(new_ast)
    assert new_code.startswith(
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.add_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.add_operator.inplace"),
    ],
)
def test_astpatch_module_changed_add_inplace_operator(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) != (module_path, new_ast)
    new_code = astunparse.unparse(new_ast)
    assert new_code.startswith(
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.add_inplace_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.str.future_import_class_str"),
        ("tests.appsec.iast.fixtures.ast.str.future_import_function_str"),
    ],
)
def test_astpatch_source_changed_with_future_imports(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) != (module_path, new_ast)
    new_code = astunparse.unparse(new_ast)
    assert new_code.startswith(
        f"""
'\\nSome\\nmulti-line\\ndocstring\\nhere\\n'
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks
import ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects
import html"""
    )
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.str.class_no_str"),
        ("tests.appsec.iast.fixtures.ast.str.function_no_str"),
        ("tests.appsec.iast.fixtures.ast.str.__init__"),  # Empty __init__.py
        ("tests.appsec.iast.fixtures.ast.str.non_utf8_content"),  # EUC-JP file content
        ("tests.appsec.iast.fixtures.ast.str.empty_file"),
        ("tests.appsec.iast.fixtures.ast.subscript.store_context"),
    ],
)
def test_astpatch_source_unchanged(module_name):
    assert ("", None) == astpatch_module(__import__(module_name, fromlist=[None]))


def test_should_iast_patch_allow_first_party():
    assert _should_iast_patch("tests.appsec.iast.integration.main")
    assert _should_iast_patch("tests.appsec.iast.integration.print_str")


def test_should_not_iast_patch_if_vendored():
    assert not _should_iast_patch("foobar.vendor.requests")
    assert not _should_iast_patch(("vendored.foobar.requests"))


def test_should_iast_patch_deny_by_default_if_third_party():
    # note that modules here must be in the ones returned by get_package_distributions()
    # but not in ALLOWLIST or DENYLIST. So please don't put astunparse there :)
    assert not _should_iast_patch("astunparse.foo.bar.not.in.deny.or.allow.list")


def test_should_not_iast_patch_if_in_denylist():
    assert not _should_iast_patch("ddtrace.internal.module")
    assert not _should_iast_patch("ddtrace.appsec._iast")
    assert not _should_iast_patch("pip.foo.bar")


def test_should_not_iast_patch_if_stdlib():
    assert not _should_iast_patch("base64")
    assert not _should_iast_patch("itertools")
    assert not _should_iast_patch("http")
    assert not _should_iast_patch("os.path")
    assert not _should_iast_patch("sys.platform")


@pytest.mark.parametrize(
    "module_name, result",
    [
        ("Envier", False),
        ("iterTools", True),
        ("functooLs", True),
        ("astunparse", False),
        ("pytest.warns", False),
        ("datetime", True),
        ("posiX", True),
        ("app", False),
        ("my_app", False),
    ],
)
def test_module_in_python_stdlib(module_name, result):
    assert _in_python_stdlib(module_name) == result


def test_module_path_none(caplog):
    with caplog.at_level(logging.DEBUG), mock.patch("ddtrace.internal.module.Path.resolve", side_effect=AttributeError):
        assert ("", None) == astpatch_module(
            __import__("tests.appsec.iast.fixtures.ast.str.class_str", fromlist=[None])
        )
        assert (
            "iast::instrumentation::ast_patching::compiling::"
            "could not find the module: tests.appsec.iast.fixtures.ast.str.class_str" in caplog.text
        )


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.io.module_stringio"),
        ("tests.appsec.iast.fixtures.ast.io.function_stringio"),
    ],
)
def test_astpatch_stringio_module_changed(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) != (module_path, new_ast)
    new_code = astunparse.unparse(new_ast)
    assert new_code.startswith(
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.stringio_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.io.module_bytesio"),
        ("tests.appsec.iast.fixtures.ast.io.function_bytesio"),
    ],
)
def test_astpatch_bytesio_module_changed(module_name):
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) != (module_path, new_ast)
    new_code = astunparse.unparse(new_ast)
    assert new_code.startswith(
        f"\nimport ddtrace.appsec._iast.taint_sinks as {_PREFIX}taint_sinks"
        f"\nimport ddtrace.appsec._iast._taint_tracking.aspects as {_PREFIX}aspects"
    )
    assert "ddtrace_aspects.bytesio_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.other.globals_builtin"),
    ],
)
def test_astpatch_globals_module_unchanged(module_name):
    """
    This is a regression test for partially matching function names:
    ``globals()`` was being incorrectly patched with the aspect for ``glob()``
    """
    module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", None) == (module_path, new_ast)


@pytest.mark.parametrize(
    "module_name, env_var",
    [
        ("tests.appsec.iast.fixtures.ast.other.with_implemented_dir", "false"),
        ("tests.appsec.iast.fixtures.ast.other.with_implemented_dir", "true"),
        ("tests.appsec.iast.fixtures.ast.other.without_implemented_dir", "false"),
        ("tests.appsec.iast.fixtures.ast.other.without_implemented_dir", "true"),
    ],
)
def test_astpatch_dir_patched_with_env_var(module_name, env_var):
    """
    Check that the ast_patching._DIR_WRAPPER code is added to the end of the module if
    the env var is False and not added otherwise
    """
    with override_env({IAST.ENV_NO_DIR_PATCH: env_var}):
        module_path, new_ast = astpatch_module(__import__(module_name, fromlist=[None]))
        assert ("", None) != (module_path, new_ast)
        new_code = astunparse.unparse(new_ast)

        if asbool(env_var):
            # If ENV_NO_DIR_PATCH is set to True our added symbols are not filtered out
            assert f"{_PREFIX}aspects" in new_code
            assert f"{_PREFIX}taint_sinks" in new_code
        else:
            # Check that the added dir code is there
            assert f"def {_PREFIX}dir" in new_code
            assert f"def {_PREFIX}set_dir_filter()" in new_code


@pytest.mark.parametrize(
    "module_name, expected_names",
    [
        (
            "tests.appsec.iast.fixtures.ast.other.with_implemented_dir",
            {"custom_added", "symbol1", "symbol2", "symbol3", "symbol4"},
        ),
        (
            "tests.appsec.iast.fixtures.ast.other.without_implemented_dir",
            {"symbol1", "symbol2", "symbol3", "symbol4"},
        ),
    ],
)
def test_astpatch_dir_patched_with_or_without_custom_dir(module_name, expected_names):
    """
    Check that the patched dir doesn't have any __ddtrace symbols and match the original
    unpatched dir() output, both with or without a previous custom __dir__ implementation
    """
    with override_env({IAST.ENV_NO_DIR_PATCH: "false"}):
        imported_mod = __import__(module_name, fromlist=[None])
        orig_dir = set(dir(imported_mod))

        for name in expected_names:
            assert name in orig_dir

        module_path, new_ast = astpatch_module(imported_mod)
        assert ("", None) != (module_path, new_ast)

        new_code = astunparse.unparse(new_ast)
        assert f"def {_PREFIX}dir" in new_code
        assert f"def {_PREFIX}set_dir_filter()" in new_code

        compiled_code = compile(new_ast, module_path, "exec")
        exec(compiled_code, imported_mod.__dict__)
        patched_dir = set(dir(imported_mod))
        for symbol in patched_dir:
            assert not symbol.startswith(_PREFIX)

        # Check that there are no new symbols that were not in the original dir() call
        assert len(patched_dir.difference(orig_dir)) == 0

        # Check that all the symbols in the expected set are in the patched dir() result
        for name in expected_names:
            assert name in patched_dir


def test_build_trie():
    from ddtrace.appsec._iast._ast.ast_patching import build_trie

    trie = build_trie(["abc", "def", "ghi", "jkl", "mno", "pqr", "stu", "vwx", "yz"])
    assert dict(trie) == {
        "a": {
            "b": {
                "c": {"": None},
            },
        },
        "d": {
            "e": {
                "f": {"": None},
            },
        },
        "g": {
            "h": {
                "i": {"": None},
            },
        },
        "j": {
            "k": {
                "l": {"": None},
            },
        },
        "m": {
            "n": {
                "o": {"": None},
            },
        },
        "p": {
            "q": {
                "r": {"": None},
            },
        },
        "s": {
            "t": {
                "u": {"": None},
            },
        },
        "v": {
            "w": {
                "x": {"": None},
            },
        },
        "y": {
            "z": {"": None},
        },
    }


def test_trie_has_string_match():
    trie = build_trie(["abc", "def", "ghi", "jkl", "mno", "pqr", "stu", "vwx", "yz"])
    assert _trie_has_prefix_for(trie, "abc")
    assert not _trie_has_prefix_for(trie, "ab")
    assert _trie_has_prefix_for(trie, "abcd")
    assert _trie_has_prefix_for(trie, "def")
    assert not _trie_has_prefix_for(trie, "de")
    assert _trie_has_prefix_for(trie, "defg")
    assert _trie_has_prefix_for(trie, "ghi")
    assert not _trie_has_prefix_for(trie, "gh")
    assert _trie_has_prefix_for(trie, "ghij")
    assert _trie_has_prefix_for(trie, "jkl")
    assert not _trie_has_prefix_for(trie, "jk")
    assert _trie_has_prefix_for(trie, "jklm")
    assert _trie_has_prefix_for(trie, "mno")
    assert not _trie_has_prefix_for(trie, "mn")
    assert _trie_has_prefix_for(trie, "mnop")
    assert _trie_has_prefix_for(trie, "pqr")
    assert not _trie_has_prefix_for(trie, "pq")
    assert _trie_has_prefix_for(trie, "pqrs")
    assert _trie_has_prefix_for(trie, "stu")
    assert not _trie_has_prefix_for(trie, "st")
    assert _trie_has_prefix_for(trie, "stuv")
    assert _trie_has_prefix_for(trie, "vwx")
    assert not _trie_has_prefix_for(trie, "vw")
    assert _trie_has_prefix_for(trie, "vwxy")
    assert _trie_has_prefix_for(trie, "yz")
    assert not _trie_has_prefix_for(trie, "y")
    assert _trie_has_prefix_for(trie, "yza")
    assert not _trie_has_prefix_for(trie, "z")
    assert not _trie_has_prefix_for(trie, "zzz")
