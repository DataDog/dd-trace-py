"""
Check that add_integration_error_log calls use constant string literals as first argument.
This script scans all Python files in ddtrace/contrib/ and reports violations.
"""

import ast
import pathlib
import sys
from typing import List
from typing import Tuple


class LogMessageChecker(ast.NodeVisitor):
    """AST visitor that checks for proper usage of add_integration_error_log."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.errors: List[Tuple[int, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Check if this is an add_integration_error_log call with non-constant first arg."""
        fn = node.func
        is_target = isinstance(fn, ast.Attribute) and fn.attr == "add_integration_error_log"

        if is_target and node.args:
            msg = node.args[0]
            is_constant_string = isinstance(msg, ast.Constant) and isinstance(msg.value, str)

            if not is_constant_string:
                self.errors.append((node.lineno, node.col_offset))

        self.generic_visit(node)


def check_file(filepath: pathlib.Path) -> List[Tuple[int, int]]:
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
        checker = LogMessageChecker(str(filepath))
        checker.visit(tree)
        return checker.errors
    except (OSError, UnicodeDecodeError) as e:
        print(f"Error reading {filepath}: {e}", file=sys.stderr)
        return []
    except SyntaxError as e:
        print(f"Syntax error in {filepath}:{e.lineno}:{e.offset}: {e.msg}", file=sys.stderr)
        return []


def main() -> int:
    contrib_path = pathlib.Path("ddtrace/contrib")
    python_files = list(contrib_path.rglob("*.py"))

    total_errors = 0

    for filepath in python_files:
        errors = check_file(filepath)
        for line_no, col_no in errors:
            print(
                f"{filepath}:{line_no}:{col_no}: "
                "LOG001 first argument to add_integration_error_log must be a constant string"
            )
            total_errors += 1

    if total_errors > 0:
        print(f"\nFound {total_errors} violation(s)", file=sys.stderr)
        return 1

    print("All add_integration_error_log calls use constant strings ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
