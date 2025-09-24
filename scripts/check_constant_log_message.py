"""
Check that log.error() and add_error_log calls use constant string literals as first argument.
This script scans all Python files in ddtrace/ and reports violations.
Exceptions can be specified in the EXCEPTIONS set using:
- "filepath:line" to exclude a specific line in a file
"""

import ast
import pathlib
import sys
from typing import List
from typing import Tuple


# Line-specific exceptions to exclude from checking
# Format: "filepath:line" to exclude a specific line in a file
EXCEPTIONS = {
    # only constant message can be log.error()
    "ddtrace/internal/telemetry/logging.py:18",
    # log.exception calls use constant messages
    "ddtrace/contrib/internal/aws_lambda/patch.py:36",
    # log.error in _probe/registry.py ends up with a log.debug()
    "ddtrace/debugging/_probe/registry.py:137",
    "ddtrace/debugging/_probe/registry.py:146",
    # we added a constant check for the wrapping method of add_error_log
    "ddtrace/appsec/_iast/_metrics.py:53",
    # we added a constant check for the wrapping method of iast_error
    "ddtrace/appsec/_iast/_logs.py:41",
    "ddtrace/appsec/_iast/_logs.py:45",
    # the non constant part is an object type
    "ddtrace/appsec/_iast/_taint_tracking/_taint_objects_base.py:75",
}


class LogMessageChecker(ast.NodeVisitor):
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.errors: List[Tuple[int, int]] = []

    def _has_send_to_telemetry_false(self, node: ast.Call) -> bool:
        """Check if the call has extra={'send_to_telemetry': False}."""
        for keyword in node.keywords:
            if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "send_to_telemetry"
                        and isinstance(value, ast.Constant)
                        and value.value is False
                    ):
                        return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        """Check if this is a log.error(), add_error_log, or iast_error call with non-constant first arg."""
        fn = node.func

        # Check for add_error_log calls
        is_add_integration_error = isinstance(fn, ast.Attribute) and fn.attr == "add_error_log"
        # Check for log.error() calls (simple check for .error() on any variable)
        is_log_error = isinstance(fn, ast.Attribute) and (fn.attr == "error" or fn.attr == "exception")
        # Check for iast_error calls
        is_iast_log = isinstance(fn, ast.Name) and (
            fn.id == "iast_error"
            or fn.id == "iast_instrumentation_ast_patching_errorr_log"
            or fn.id == "iast_propagation_error_log"
        )
        is_target = is_add_integration_error or is_log_error or is_iast_log

        if is_target and node.args:
            msg = node.args[0]
            is_constant_string = isinstance(msg, ast.Constant) and isinstance(msg.value, str)

            # Skip constant string check if send_to_telemetry is False for log.error/exception calls
            if not is_constant_string and is_log_error and self._has_send_to_telemetry_false(node):
                pass
            elif not is_constant_string and not self._is_line_exception(node.lineno):
                self.errors.append((node.lineno, node.col_offset))

        self.generic_visit(node)

    def _is_line_exception(self, line_no: int) -> bool:
        """Check if this specific line is in the exceptions list."""
        return f"{str(self.filepath)}:{line_no}" in EXCEPTIONS


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
    contrib_path = pathlib.Path("ddtrace")
    python_files = list(contrib_path.rglob("*.py"))

    total_errors = 0

    for filepath in python_files:
        errors = check_file(filepath)
        for line_no, col_no in errors:
            print(f"{filepath}:{line_no}:{col_no}: " "LOG001 first argument to logging call must be a constant string")
            total_errors += 1

    if total_errors > 0:
        print(f"\nFound {total_errors} violation(s)", file=sys.stderr)
        return 1

    print("All logging calls use constant strings ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
