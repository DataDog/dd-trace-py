import os

#!/usr/bin/env python3
"""
Debug script to print the patched code of a Python file, optionally with syntax highlighting (with pygments) and code formatting (with black).
Usage: PYTHONPATH=$PYTHONPATH:~/.../dd-trace-py/ python -m scripts.iast.print_patched_code /path/to/your/python-file.py
"""

import sys
from typing import Text
import sys

try:
    from pygments import highlight
    from pygments.lexers import PythonLexer
    from pygments.formatters import TerminalFormatter

    colorize = True
except ImportError:
    colorize = False

try:
    import black

    format = True
except ImportError:
    format = False

from ddtrace.appsec._iast._ast.ast_patching import astpatch_module


def _get_patched_code(module_path: Text, module_name: Text) -> str:
    """
    Print the patched code to stdout, for debugging purposes.
    Tips: have pygments and black installed for a nicer output.
    """
    import astunparse

    module_dir = os.path.dirname(module_path)
    sys.path.append(module_dir)
    _, new_ast = astpatch_module(__import__(module_name))
    return astunparse.unparse(new_ast)


if __name__ == "__main__":
    MODULE_PATH = sys.argv[1]
    MODULE_NAME = sys.argv[2]
    code = _get_patched_code(MODULE_PATH, MODULE_NAME)

    if format:
        code = black.format_str(code, mode=black.FileMode())

    if colorize:
        code = highlight(code, PythonLexer(), TerminalFormatter())

    print(code)
