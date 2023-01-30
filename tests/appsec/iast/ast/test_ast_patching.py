#!/usr/bin/env python3
import pytest

from ddtrace.appsec.iast.ast.ast_patching import visit_ast
from ddtrace.appsec.iast.ast.ast_patching import astpatch_source


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print('hi')", "test.py", "test"),
        ("print('str')", "test.py", "test"),
        ("str", "test.py", "test"),
        ("print('hi' + 'bye')", "test.py", "test"),
    ],
)
def test_visit_ast_unchanged(source_text, module_path, module_name):
    """
    Source texts not containing:
    - str() calls
    - [...]  // To be filled with more aspects
    won't be modified by ast patching, so will return empty string
    """
    assert "" == visit_ast(source_text, module_path, module_name)


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print(str('hi'))", "test.py", "test"),
        ("print(str('hi' + 'bye'))", "test.py", "test"),
    ],
)
def test_visit_ast_changed(source_text, module_path, module_name):
    """
    Source texts containing:
    - str() calls
    - [...]  // To be filled with more aspects
    will be modified by ast patching, so will not return empty string
    """
    assert "" != visit_ast(source_text, module_path, module_name)


@pytest.mark.parametrize(
    "module_path, module_name",
    [
        ("tests/appsec/iast/fixtures/aspects/str/function_str.py", "function_str"),
        ("tests/appsec/iast/fixtures/aspects/str/class_str.py", "class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.function_str"),
    ],
)
def test_astpatch_source_changed(module_path, module_name):
    assert ("", "") != astpatch_source(module_path, module_name)


@pytest.mark.parametrize(
    "module_path, module_name",
    [
        ("tests/appsec/iast/fixtures/aspects/str/function_no_str.py", "function_str"),
        ("tests/appsec/iast/fixtures/aspects/str/class_no_str.py", "class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.class_no_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.function_no_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str"),  # Empty __init__.py
        (None, "tests.appsec.iast.fixtures.aspects.str.non_utf8_content"),  # Empty __init__.py
    ],
)
def test_astpatch_source_unchanged(module_path, module_name):
    assert ("", "") == astpatch_source(module_path, module_name)
