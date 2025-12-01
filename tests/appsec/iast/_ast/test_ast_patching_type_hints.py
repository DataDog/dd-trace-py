# -*- encoding: utf-8 -*-
from ast import unparse
from typing import Text


def _get_patched_code(module_path: Text, module_name: Text) -> str:
    """
    Print the patched code to stdout, for debugging purposes.
    """
    from ddtrace.appsec._iast._ast.ast_patching import visit_ast

    with open(module_path, "rb") as source_file:
        source_text = source_file.read()

        new_source = visit_ast(
            source_text,
            module_path,
            module_name=module_name,
        )

        # If no modifications are done,
        # visit_ast returns None
        if not new_source:
            return ""

        new_code = unparse(new_source)
        return new_code


def test_no_index_aspects():
    """
    Methods should not be replaced by the aspect since it's not the builtin method
    """
    patched_code = _get_patched_code("tests/appsec/iast/_ast/fixtures/annotated_code.py", "annotated_code")

    # Makes sure no subscripts are patched:
    assert "index_aspect" not in patched_code

    # Makes sure all add operators are patched:
    assert "ddtrace_aspects.add_aspect" in patched_code
    assert "+" not in patched_code
