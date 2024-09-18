#!/usr/bin/env python3
import logging

import astunparse
import mock
import pytest

from ddtrace.appsec._iast._ast.ast_patching import _in_python_stdlib
from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from ddtrace.appsec._iast._ast.ast_patching import visit_ast


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
    module_path, new_source = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith(
        "\nimport ddtrace.appsec._iast.taint_sinks as ddtrace_taint_sinks"
        "\nimport ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects"
    )
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.add_operator.basic"),
    ],
)
def test_astpatch_module_changed_add_operator(module_name):
    module_path, new_source = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith(
        "\nimport ddtrace.appsec._iast.taint_sinks as ddtrace_taint_sinks"
        "\nimport ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects"
    )
    assert "ddtrace_aspects.add_aspect(" in new_code


@pytest.mark.parametrize(
    "module_name",
    [
        ("tests.appsec.iast.fixtures.ast.add_operator.inplace"),
    ],
)
def test_astpatch_module_changed_add_inplace_operator(module_name):
    module_path, new_source = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith(
        "\nimport ddtrace.appsec._iast.taint_sinks as ddtrace_taint_sinks"
        "\nimport ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects"
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
    module_path, new_source = astpatch_module(__import__(module_name, fromlist=[None]))
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith(
        """
'\\nSome\\nmulti-line\\ndocstring\\nhere\\n'
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import ddtrace.appsec._iast.taint_sinks as ddtrace_taint_sinks
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
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
    assert ("", "") == astpatch_module(__import__(module_name, fromlist=[None]))


def test_module_should_iast_patch():
    assert not _should_iast_patch("ddtrace.internal.module")
    assert not _should_iast_patch("ddtrace.appsec._iast")
    assert not _should_iast_patch("base64")
    assert not _should_iast_patch("envier")
    assert not _should_iast_patch("itertools")
    assert not _should_iast_patch("http")
    assert _should_iast_patch("tests.appsec.iast.integration.main")
    assert _should_iast_patch("tests.appsec.iast.integration.print_str")


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
        assert ("", "") == astpatch_module(__import__("tests.appsec.iast.fixtures.ast.str.class_str", fromlist=[None]))
        assert "astpatch_source couldn't find the module: tests.appsec.iast.fixtures.ast.str.class_str" in caplog.text
