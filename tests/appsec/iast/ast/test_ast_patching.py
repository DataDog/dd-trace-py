#!/usr/bin/env python3
import sys

import pytest


if sys.version_info.major >= 3:
    import astunparse

    from ddtrace.appsec.iast._ast.ast_patching import astpatch_source
    from ddtrace.appsec.iast._ast.ast_patching import visit_ast


@pytest.mark.parametrize(
    "source_text, module_path, module_name",
    [
        ("print('hi')", "test.py", "test"),
        ("print('str')", "test.py", "test"),
        ("str", "test.py", "test"),
        ("print('hi' + 'bye')", "test.py", "test"),
    ],
)
@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 3 only")
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
@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 3 only")
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
@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 3 only")
def test_astpatch_source_changed(module_path, module_name):
    module_path, new_source = astpatch_source(module_path, module_name)
    assert ("", "") != (module_path, new_source)
    new_code = astunparse.unparse(new_source)
    assert new_code.startswith("\nimport ddtrace.appsec.iast._ast.aspects as ddtrace_aspects")
    assert "ddtrace_aspects.str_aspect(" in new_code


@pytest.mark.parametrize(
    "module_path, module_name",
    [
        ("tests/appsec/iast/fixtures/aspects/str/function_no_str.py", "function_str"),
        ("tests/appsec/iast/fixtures/aspects/str/class_no_str.py", "class_str"),
        ("tests/appsec/iast/fixtures/aspects/str/non_existent_invented_extension.cppy", "class_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.class_no_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str.function_no_str"),
        (None, "tests.appsec.iast.fixtures.aspects.str"),  # Empty __init__.py
        (None, "tests.appsec.iast.fixtures.aspects.str.non_utf8_content"),  # EUC-JP file content
    ],
)
def test_astpatch_source_unchanged(module_path, module_name):
    assert ("", "") == astpatch_source(module_path, module_name)


@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 3 only")
def test_astpatch_source_raises_exception():
    with pytest.raises(Exception) as e:
        astpatch_source(None, None)

    assert e.value.args == ("Implementation Error: You must pass module_name and, optionally, module_path",)
