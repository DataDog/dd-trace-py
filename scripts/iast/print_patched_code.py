#!/usr/bin/env python3

"""
Debug script to print the patched code of a Python file, optionally colorized (if you have pygments)
and formatted (if you have black).
Usage: PYTHONPATH=$PYTHONPATH:~/.../dd-trace-py/ python -m scripts.iast.print_patched_code /path/to/your/python_file.py
"""

import os
import sys
from typing import Text


try:
    from pygments import highlight
    from pygments.formatters import TerminalFormatter
    from pygments.lexers import PythonLexer

    colorize = True
except ImportError:
    colorize = False

try:
    import black

    _format = True
except ImportError:
    _format = False

from ddtrace.appsec._iast._ast.ast_patching import astpatch_module


def _get_patched_code(module_path: Text) -> str:
    import astunparse

    module_dir = os.path.dirname(module_path)
    sys.path.append(module_dir)
    module_name = os.path.splitext(os.path.basename(module_path))[0]
    _, new_ast = astpatch_module(__import__(module_name))
    return astunparse.unparse(new_ast)


if __name__ == "__main__":
    MODULE_PATH = sys.argv[1]
    code = _get_patched_code(MODULE_PATH)

    if _format:
        code = black.format_str(code, mode=black.FileMode())

    if colorize:
        code = highlight(code, PythonLexer(), TerminalFormatter())

    print(code)
