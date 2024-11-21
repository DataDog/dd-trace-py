#!/usr/bin/env python3
"""
Debug script to print the patched code of a Python file.
Usage: PYTHONPATH=$PYTHONPATH:~/.../dd-trace-py/ python -m scripts.iast.print_patched_code /path/to/your/python-file.py
"""

import sys
from typing import Text


def _get_patched_code(module_path: Text, module_name: Text) -> str:
    """
    Print the patched code to stdout, for debugging purposes.
    """
    import astunparse

    from ddtrace.appsec._iast._ast.ast_patching import get_encoding
    from ddtrace.appsec._iast._ast.ast_patching import visit_ast

    with open(module_path, "r", encoding=get_encoding(module_path)) as source_file:
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

        new_code = astunparse.unparse(new_source)
        return new_code


if __name__ == "__main__":
    MODULE_PATH = sys.argv[1]
    print(_get_patched_code(MODULE_PATH, "MOCK_MODULE_NAME"))
