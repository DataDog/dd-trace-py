from collections import defaultdict
from collections import deque
import linecache
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.compat import Path
from ddtrace.internal.coverage._native import replace_in_tuple
from ddtrace.internal.coverage.instrumentation import instrument_all_lines
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.vendor.contextvars import ContextVar


CWD = Path.cwd()

_original_exec = exec

ctx_covered = ContextVar("ctx_covered", default=None)
ctx_coverage_enabed = ContextVar("ctx_coverage_enabled", default=False)


def collapse_ranges(numbers):
    # This function turns an ordered list of numbers into a list of ranges.
    # For example, [1, 2, 3, 5, 6, 7, 9] becomes [(1, 3), (5, 7), (9, 9)]
    # WARNING: Written by Copilot
    if not numbers:
        return ""
    ranges = []
    start = end = numbers[0]
    for number in numbers[1:]:
        if number == end + 1:
            end = number
        else:
            ranges.append((start, end))
            start = end = number

    ranges.append((start, end))

    return ranges


def collect_code_objects(code: CodeType) -> t.Iterator[t.Tuple[CodeType, t.Optional[CodeType]]]:
    # Topological sorting
    q = deque([code])
    g = {}
    p = {}
    leaves: t.Deque[CodeType] = deque()

    # Build the graph and the parent map
    while q:
        c = q.popleft()
        new_codes = g[c] = {_ for _ in c.co_consts if isinstance(_, CodeType)}
        if not new_codes:
            leaves.append(c)
            continue
        for new_code in new_codes:
            p[new_code] = c
        q.extend(new_codes)

    # Yield the code objects in topological order
    while leaves:
        c = leaves.popleft()
        parent = p.get(c)
        yield c, parent
        if parent is not None:
            children = g[parent]
            children.remove(c)
            if not children:
                leaves.append(parent)


class ModuleCodeCollector(BaseModuleWatchdog):
    _instance: t.Optional["ModuleCodeCollector"] = None

    def __init__(self):
        super().__init__()
        self.seen = set()
        self.coverage_enabled = False
        self.lines = defaultdict(set)
        self.covered = defaultdict(set)

        # Replace the built-in exec function with our own in the pytest globals
        try:
            import _pytest.assertion.rewrite as par

            par.exec = self._exec
        except ImportError:
            pass

    def hook(self, arg):
        path, line = arg
        if self.coverage_enabled:
            lines = self.covered[path]
            if line not in lines:
                # This line has already been covered
                lines.add(line)

        if ctx_coverage_enabed.get():
            ctx_lines = ctx_covered.get()[path]
            if line not in ctx_lines:
                ctx_lines.add(line)

    @classmethod
    def report(cls, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        import os
        import re

        try:
            w, _ = os.get_terminal_size()
        except OSError:
            w = 80

        NOCOVER_PRAGMA_RE = re.compile(r"^\s*(?P<command>.*)\s*#.*\s+pragma\s*:\s*no\s?cover.*$")

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
                    import ast

                    with open(path, "r") as f:
                        file_src = f.read()
                    parsed = ast.parse(file_src)
                    statement = find_statement_for_line(parsed, src_line)
                    if statement is not None:
                        return statement.lineno, statement.end_lineno
                    # We shouldn't get here, in theory, but if we do, let's not consider anything uncovered.
                    return None
                # If our line does not end in ':', assume it's just one line that needs to be removed
                return src_line, src_line
            return None

        total_executable_lines = 0
        total_covered_lines = 0
        total_missed_lines = 0
        n = max(len(path) for path in instance.lines) + 4

        covered_lines = instance._get_covered_lines()

        # Title
        print(" DATADOG LINE COVERAGE REPORT ".center(w, "="))

        # Header
        print(f"{'PATH':<{n}}{'LINES':>8}{'MISSED':>8} {'COVERED':>8}  MISSED LINES")
        print("-" * (w))

        for path, orig_lines in sorted(instance.lines.items()):
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
            print(f"{path:{n}s}{n_lines:>8}{n_missed:>8}{int(n_covered/n_lines * 100):>8}%{missed_str}")
        print("-" * (w))
        total_covered_percent = int((total_covered_lines / total_executable_lines) * 100)
        print(f"{'TOTAL':<{n}}{total_executable_lines:>8}{total_missed_lines:>8}{total_covered_percent:>8}%")
        print()

    def _get_covered_lines(self):
        if ctx_coverage_enabed.get(False):
            return ctx_covered.get()
        return self.covered

    class CollectInContext:
        def __enter__(self):
            ctx_covered.set(defaultdict(set))
            ctx_coverage_enabed.set(True)

        def __exit__(self, *args, **kwargs):
            ctx_coverage_enabed.set(False)

    @classmethod
    def start_coverage(cls):
        if cls._instance is None:
            return
        cls._instance.coverage_enabled = True

    @classmethod
    def stop_coverage(cls):
        if cls._instance is None:
            return
        cls._instance.coverage_enabled = False

    @classmethod
    def coverage_enabled(cls):
        if ctx_coverage_enabed.get():
            return True
        if cls._instance is None:
            return False
        return cls._instance.coverage_enabled

    @classmethod
    def report_seen_lines(cls):
        """Generate the same data as expected by ddtrace.ci_visibility.coverage.build_payload:

        if input_path is provided, filter files to only include that path, and make it relative to said path

        "files": [
            {
                "filename": <String>,
                "segments": [
                    [Int, Int, Int, Int, Int],  # noqa:F401
                ]
            },
            ...
        ]
        """
        if cls._instance is None:
            return []
        files = []
        if ctx_coverage_enabed.get():
            covered = ctx_covered.get()
        else:
            covered = cls._instance.covered
        for path, lines in covered.items():
            sorted_lines = sorted(lines)
            collapsed_ranges = collapse_ranges(sorted_lines)
            file_segments = []
            for file_segment in collapsed_ranges:
                file_segments.append([file_segment[0], 0, file_segment[1], 0, -1])
            files.append({"filename": path, "segments": file_segments})

        return files

    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        code_path = Path(code.co_filename).resolve()
        # TODO: Remove hardcoded paths
        if not code_path.is_relative_to(CWD):
            # Not a code object we want to instrument
            return code

        # Recursively instrument nested code objects, in topological order
        # DEV: We need to make a list of the code objects because when we start
        # mutating the parent code objects, the hashes maintained by the
        # generator will be invalidated.
        for nested_code, parent_code in list(collect_code_objects(code)):
            # Instrument the code object
            new_code = self.instrument_code(nested_code)

            # If it has a parent, update the parent's co_consts to point to the
            # new code object.
            if parent_code is not None:
                replace_in_tuple(parent_code.co_consts, nested_code, new_code)

        return new_code

    def after_import(self, _module: ModuleType) -> None:
        pass

    def instrument_code(self, code: CodeType) -> CodeType:
        # Avoid instrumenting the same code object multiple times
        if code in self.seen:
            return code
        self.seen.add(code)

        path = str(Path(code.co_filename).resolve().relative_to(CWD))

        new_code, lines = instrument_all_lines(code, self.hook, path)

        # Keep note of all the lines that have been instrumented. These will be
        # the ones that can be covered.
        self.lines[path] |= lines

        return new_code

    def _exec(self, _object, _globals=None, _locals=None, **kwargs):
        # The pytest module loader doesn't implement a get_code method so we
        # need to intercept the loading of test modules by wrapping around the
        # exec built-in function.
        new_object = (
            self.transform(_object, None)
            if isinstance(_object, CodeType) and _object.co_name == "<module>"
            else _object
        )

        # Execute the module before calling the after_import hook
        _original_exec(new_object, _globals, _locals, **kwargs)

    @classmethod
    def uninstall(cls) -> None:
        # Restore the original exec function
        try:
            import _pytest.assertion.rewrite as par

            par.exec = _original_exec
        except ImportError:
            pass

        return super().uninstall()
