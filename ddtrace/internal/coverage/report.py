import ast
import linecache
import os
import re
import typing as t

from ddtrace.internal.coverage.util import collapse_ranges


try:
    w, _ = os.get_terminal_size()
except OSError:
    w = 80

NOCOVER_PRAGMA_RE = re.compile(r"^\s*(?P<command>.*)\s*#.*\s+pragma\s*:\s*no\s?cover.*$")

ast_cache: t.Dict[str, t.Any] = {}


def _get_ast_for_path(path: str):
    if path not in ast_cache:
        with open(path, "r") as f:
            file_src = f.read()
        ast_cache[path] = ast.parse(file_src)
    return ast_cache[path]


def find_statement_for_line(node, line):
    if hasattr(node, "body"):
        for child_node in node.body:
            found_node = find_statement_for_line(child_node, line)
            if found_node is not None:
                return found_node

    # If the start and end line numbers are the same, we're (almost certainly) dealing with some kind of
    # statement instead of the sort of block statements we're looking for.
    if node.lineno == node.end_lineno:
        return None

    if node.lineno <= line <= node.end_lineno:
        return node

    return None


def no_cover(path, src_line) -> t.Optional[t.Tuple[int, int]]:
    """Returns the start and end lines of statements to ignore the line includes pragma nocover.

    If the line ends with a :, parse the AST and return the block the line belongs to.
    """
    text = linecache.getline(path, src_line).strip()
    matches = NOCOVER_PRAGMA_RE.match(text)
    if matches:
        if matches["command"].strip().endswith(":"):
            parsed = _get_ast_for_path(path)
            statement = find_statement_for_line(parsed, src_line)
            if statement is not None:
                return statement.lineno, statement.end_lineno
            # We shouldn't get here, in theory, but if we do, let's not consider anything uncovered.
            return None
        # If our line does not end in ':', assume it's just one line that needs to be removed
        return src_line, src_line
    return None


def print_coverage_report(executable_lines, covered_lines, ignore_nocover=False):
    total_executable_lines = 0
    total_covered_lines = 0
    total_missed_lines = 0
    n = max(len(path) for path in executable_lines) + 4

    covered_lines = covered_lines

    # Title
    print(" DATADOG LINE COVERAGE REPORT ".center(w, "="))

    # Header
    print(f"{'PATH':<{n}}{'LINES':>8}{'MISSED':>8} {'COVERED':>8}  MISSED LINES")
    print("-" * (w))

    for path, orig_lines in sorted(executable_lines.items()):
        path_lines = orig_lines.copy()
        path_covered = covered_lines[path].copy()
        if not ignore_nocover:
            for line in orig_lines:
                # We may have already deleted this line due to no_cover
                if line not in path_lines and line not in path_covered:
                    continue
                no_cover_lines = no_cover(path, line)
                if no_cover_lines:
                    for no_cover_line in range(no_cover_lines[0], no_cover_lines[1] + 1):
                        path_lines.discard(no_cover_line)
                        path_covered.discard(no_cover_line)

        n_lines = len(path_lines)
        n_covered = len(path_covered)
        n_missed = n_lines - n_covered
        total_executable_lines += n_lines
        total_covered_lines += n_covered
        total_missed_lines += n_missed
        if n_covered == 0:
            continue
        missed_ranges = collapse_ranges(sorted(path_lines - path_covered))
        missed = ",".join([f"{start}-{end}" if start != end else str(start) for start, end in missed_ranges])
        missed_str = f"  [{missed}]" if missed else ""
        print(f"{path:{n}s}{n_lines:>8}{n_missed:>8}{int(n_covered / n_lines * 100):>8}%{missed_str}")
    print("-" * (w))
    total_covered_percent = int((total_covered_lines / total_executable_lines) * 100)
    print(f"{'TOTAL':<{n}}{total_executable_lines:>8}{total_missed_lines:>8}{total_covered_percent:>8}%")
    print()
