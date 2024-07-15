from collections import defaultdict
import json
import os
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.compat import Path
from ddtrace.internal.coverage.instrumentation import instrument_all_lines
from ddtrace.internal.coverage.report import gen_json_report
from ddtrace.internal.coverage.report import print_coverage_report
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.vendor.contextvars import ContextVar


_original_exec = exec

ctx_covered = ContextVar("ctx_covered", default=None)
ctx_coverage_enabled = ContextVar("ctx_coverage_enabled", default=False)


class ModuleCodeCollector(BaseModuleWatchdog):
    _instance: t.Optional["ModuleCodeCollector"] = None

    def __init__(self) -> None:
        super().__init__()
        self.seen: t.Set = set()
        self._coverage_enabled: bool = False
        self.lines: t.DefaultDict[str, t.Set] = defaultdict(set)
        self.covered: t.DefaultDict[str, t.Set] = defaultdict(set)
        self._include_paths: t.List[Path] = []
        self.lines_by_context: t.DefaultDict[str, t.DefaultDict[str, t.Set]] = defaultdict(lambda: defaultdict(set))

        # Replace the built-in exec function with our own in the pytest globals
        try:
            import _pytest.assertion.rewrite as par

            par.exec = self._exec
        except ImportError:
            pass

    @classmethod
    def install(cls, include_paths: t.Optional[t.List[Path]] = None):
        if ModuleCodeCollector.is_installed():
            return

        super().install()

        if cls._instance is None:
            # installation failed
            return

        if include_paths is None:
            include_paths = [Path(os.getcwd())]

        cls._instance._include_paths = include_paths

    def hook(self, arg):
        line, path = arg

        if self._coverage_enabled:
            lines = self.covered[path]
            if line not in lines:
                # This line has already been covered
                lines.add(line)

        if ctx_coverage_enabled.get():
            ctx_lines = ctx_covered.get()[path]
            if line not in ctx_lines:
                ctx_lines.add(line)

    def absorb_data_json(self, data_json: str):
        """Absorb a JSON report of coverage data. This is used to aggregate coverage data from multiple processes.

        Absolute paths are expected.
        """
        data = json.loads(data_json)
        for path, lines in data["lines"].items():
            self.lines[path] |= set(lines)
        for path, covered in data["covered"].items():
            if self._coverage_enabled:
                self.covered[path] |= set(covered)
            if ctx_coverage_enabled.get():
                ctx_covered.get()[path] |= set(covered)

    @classmethod
    def report(cls, workspace_path: Path, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        executable_lines = instance.lines
        covered_lines = instance._get_covered_lines()

        print_coverage_report(executable_lines, covered_lines, workspace_path, ignore_nocover=ignore_nocover)

    @classmethod
    def get_data_json(cls) -> str:
        if cls._instance is None:
            return "{}"
        instance: ModuleCodeCollector = cls._instance

        executable_lines = {path: list(lines) for path, lines in instance.lines.items()}
        covered_lines = {path: list(lines) for path, lines in instance._get_covered_lines().items()}

        return json.dumps({"lines": executable_lines, "covered": covered_lines})

    @classmethod
    def get_context_data_json(cls) -> str:
        covered_lines = cls.get_context_covered_lines()

        return json.dumps({"lines": {}, "covered": {path: list(lines) for path, lines in covered_lines.items()}})

    @classmethod
    def get_context_covered_lines(cls):
        if cls._instance is None or not ctx_coverage_enabled.get():
            return {}

        return ctx_covered.get()

    @classmethod
    def write_json_report_to_file(cls, filename: str, workspace_path: Path, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        executable_lines = instance.lines
        covered_lines = instance._get_covered_lines()

        with open(filename, "w") as f:
            f.write(gen_json_report(executable_lines, covered_lines, workspace_path, ignore_nocover=ignore_nocover))

    def _get_covered_lines(self) -> t.Dict[str, t.Set[int]]:
        if ctx_coverage_enabled.get(False):
            return ctx_covered.get()
        return self.covered

    class CollectInContext:
        def __enter__(self):
            ctx_covered.set(defaultdict(set))
            ctx_coverage_enabled.set(True)
            return self

        def __exit__(self, *args, **kwargs):
            ctx_coverage_enabled.set(False)

        def get_covered_lines(self):
            return ctx_covered.get()

    @classmethod
    def start_coverage(cls):
        if cls._instance is None:
            return
        cls._instance._coverage_enabled = True

    @classmethod
    def stop_coverage(cls):
        if cls._instance is None:
            return
        cls._instance._coverage_enabled = False

    @classmethod
    def coverage_enabled(cls):
        if ctx_coverage_enabled.get():
            return True
        if cls._instance is None:
            return False
        return cls._instance._coverage_enabled

    @classmethod
    def coverage_enabled_in_context(cls):
        return cls._instance is not None and ctx_coverage_enabled.get()

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
        covered = cls._instance._get_covered_lines()

        for path, lines in covered.items():
            sorted_lines = sorted(lines)
            collapsed_ranges = collapse_ranges(sorted_lines)
            file_segments = []
            for file_segment in collapsed_ranges:
                file_segments.append([file_segment[0], 0, file_segment[1], 0, -1])
            files.append({"filename": path, "segments": file_segments})

        return files

    def transform(self, code: CodeType, _module: ModuleType) -> CodeType:
        code_path = Path(code.co_filename)

        if not any(code_path.is_relative_to(include_path) for include_path in self._include_paths):
            # Not a code object we want to instrument
            return code

        return self.instrument_code(code)

    def after_import(self, _module: ModuleType) -> None:
        pass

    def instrument_code(self, code: CodeType) -> CodeType:
        # Avoid instrumenting the same code object multiple times
        if code in self.seen:
            return code
        self.seen.add(code)

        new_code, lines = instrument_all_lines(code, self.hook, code.co_filename)

        # Keep note of all the lines that have been instrumented. These will be
        # the ones that can be covered.
        self.lines[code.co_filename] |= lines

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
