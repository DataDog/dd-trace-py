from collections import defaultdict
from collections import deque
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.compat import Path
from ddtrace.internal.coverage._native import replace_in_tuple
from ddtrace.internal.coverage.instrumentation import instrument_all_lines
from ddtrace.internal.module import BaseModuleWatchdog


CWD = Path.cwd()

_original_exec = exec


def collect_code_objects(code: CodeType) -> t.Iterator[t.Tuple[CodeType, CodeType]]:
    q = deque([code])
    while q:
        c = q.popleft()
        for next_code in (_ for _ in c.co_consts if isinstance(_, CodeType)):
            q.append(next_code)
            yield (next_code, c)


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
        if line in self.covered[path]:
            # This line has already been covered
            return

        # Take note of the line that was covered
        self.covered[path].add(line)

    def report(self):
        print("COVERAGE REPORT:")
        for path, lines in sorted(self.lines.items()):
            n_covered = len(self.covered[path])
            if n_covered == 0:
                continue
            print(f"{path:60s} {int(n_covered/len(lines) * 100)}%")

    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        code_path = Path(code.co_filename).resolve()
        # TODO: Remove hardcoded paths
        if all(not code_path.is_relative_to(CWD / folder) for folder in ("starlette", "tests")):
            # Not a code object we want to instrument
            return code

        # Transform the module code object
        new_code = self.instrument_code(code)

        # Recursively instrument nested code objects
        for nested_code, parent_code in collect_code_objects(new_code):
            replace_in_tuple(parent_code.co_consts, nested_code, self.instrument_code(nested_code))

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
