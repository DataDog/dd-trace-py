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


CWD = Path.cwd()

_original_exec = exec


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
            ranges.append(f"{start}-{end}" if start != end else str(end))
            start = end = number

    ranges.append(f"{start}-{end}" if start != end else str(end))

    return ", ".join(ranges)


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
    def __init__(self):
        super().__init__()
        self.seen = set()
        self.lines = defaultdict(set)
        self.covered = defaultdict(set)

        import atexit

        atexit.register(self.report)  # Quick and dirty coverage report

        # Replace the built-in exec function with our own in the pytest globals
        try:
            import _pytest.assertion.rewrite as par

            par.exec = self._exec
        except ImportError:
            pass

    def hook(self, arg):
        path, line = arg
        lines = self.covered[path]
        if line in lines:
            # This line has already been covered
            return

        # Take note of the line that was covered
        lines.add(line)

    def report(self):
        import os

        try:
            w, _ = os.get_terminal_size()
        except OSError:
            w = 80

        def no_cover(path, line):
            text = linecache.getline(path, line).strip()
            _, _, comment = text.partition("#")
            if comment:
                command, _, option = comment[1:].strip().partition(":")
                return command.strip() == "pragma" and option.strip() in {"nocover", "no cover"}
            return False

        # Title
        print(" DATADOG LINE COVERAGE REPORT ".center(w, "="))

        n = max(len(path) for path in self.lines) + 4

        # Header
        print(f"{'PATH':<{n}}{'LINES':>8}{'MISSED':>8} {'COVERED':>8}  MISSED LINES")
        print("-" * w)

        total_lines = total_missed = 0
        for path, lines in sorted(self.lines.items()):
            covered = self.covered[path]
            for line in list(lines):
                if no_cover(path, line):
                    lines -= {line}
                    covered -= {line}
            n_covered = len(covered)
            if n_covered == 0:
                continue
            missed = collapse_ranges(sorted(lines - covered))
            print(
                f"{path:{n}s}{len(lines):>8}{len(lines)-n_covered:>8}{int(n_covered/len(lines) * 100):>8}%  [{missed}]"
            )
            total_lines += len(lines)
            total_missed += len(lines) - n_covered

        # Footer
        print("-" * w)
        covered = int((total_lines - total_missed) / total_lines * 100) if total_lines else 100
        print(f"{'TOTAL':<{n}}{total_lines:>8}{total_missed:>8}{covered:>8}%")

    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        code_path = Path(code.co_filename).resolve()
        # TODO: Remove hardcoded paths
        if all(not code_path.is_relative_to(CWD / folder) for folder in ("starlette", "tests")):
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
