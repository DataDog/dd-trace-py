import multiprocessing
import multiprocessing.process
from collections import defaultdict
from collections import deque
import os
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal.compat import Path
from ddtrace.internal.coverage._native import replace_in_tuple
from ddtrace.internal.coverage.instrumentation import instrument_all_lines
from ddtrace.internal.coverage.report import get_json_report
from ddtrace.internal.coverage.report import print_coverage_report
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.module import BaseModuleWatchdog
from ddtrace.vendor.contextvars import ContextVar


_original_exec = exec

ctx_covered = ContextVar("ctx_covered", default=None)
ctx_coverage_enabed = ContextVar("ctx_coverage_enabled", default=False)

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
        self._include_paths: t.List[Path] = []
        self.lines_by_context = defaultdict(lambda: defaultdict(set))
        self._use_coverage_queue = False
        self._coverage_queue = None

        # Replace the built-in exec function with our own in the pytest globals
        try:
            import _pytest.assertion.rewrite as par

            par.exec = self._exec
        except ImportError:
            pass

    @classmethod
    def install(cls, include_paths: t.Optional[t.List[Path]] = None, coverage_queue=None):
        print(f"ROMAIN INSTALLING IN PROCESS {os.getpid()=}, {coverage_queue=}")
        if(ModuleCodeCollector.is_installed()):
            print(f"ROMAIN ALREADY INSTALLED IN PROCESS {os.getpid()=}")
            return

        super().install()

        if not include_paths:
            include_paths = Path(os.getcwd())

        cls._instance._include_paths = include_paths
        if coverage_queue is not None:
            print("ROMAIN INSTALLING WITH QUEUE")
            cls._instance._use_coverage_queue = True
            cls._instance._coverage_queue = coverage_queue

        print(f"ROMAIN INSTALLED IN PROCESS {os.getpid()=}")

        _patch_multiprocessing()


        print(f"ROMAIN (RE)PATCHED MULTIPROCESSING {os.getpid()=}")

        def _forksafe_hook():
            with open(f"forksafe_{os.getpid()}.log", "w") as f:
                f.write("ROMAIN FORKSAFE HOOK CALLED\n")

        forksafe.register(_forksafe_hook)

    def hook(self, arg):
        path, line = arg

        print(f"ROMAIN HOOK CALLED {self.coverage_enabled=}, {ctx_coverage_enabed.get()=}, {os.getpid()=}")

        if self._use_coverage_queue:
            print(f"ROMAIN USING QUEUE {os.getpid()=}, {path=}, {line=}")
            self._coverage_queue.put((path, line))
            return


        if self.coverage_enabled:
            lines = self.covered[path]
            if line not in lines:
                # This line has already been covered
                lines.add(line)

        if ctx_coverage_enabed.get():
            # from ddtrace import tracer
            # current_id = tracer.current_span().trace_id
            #
            # current_ctx_lines = self.lines_by_context[current_id]
            # current_ctx_lines[path].add(line)

            ctx_lines = ctx_covered.get()[path]
            if line not in ctx_lines:
                ctx_lines.add(line)

    @classmethod
    def report(cls, workspace_path: Path, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        executable_lines = instance.lines
        covered_lines = instance._get_covered_lines()

        print_coverage_report(executable_lines, covered_lines, workspace_path, ignore_nocover=ignore_nocover)

    @classmethod
    def write_json_report_to_file(cls, filename: str, workspace_path: Path, ignore_nocover: bool = False):
        if cls._instance is None:
            return
        instance: ModuleCodeCollector = cls._instance

        executable_lines = instance.lines
        covered_lines = instance._get_covered_lines()

        with open(filename, "w") as f:
            f.write(get_json_report(executable_lines, covered_lines, workspace_path, ignore_nocover=ignore_nocover))

    def _get_covered_lines(self) -> t.Dict[str, t.Set[int]]:
        if ctx_coverage_enabed.get(False):

            # from ddtrace import tracer
            # current_id = tracer.current_span().trace_id
            #
            # return self.lines_by_context[current_id]

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
        print(f"ROMAIN COVERAGE STARTED {os.getpid()=}")

        if cls._instance is None:
            print(f"ROMAIN INSTANCE IS NULL IN START_COVERAGE {os.getpid()=}")
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

        if code.co_filename.endswith("app.py"):
            print("CODE HASH: ", hash(code))

        if code in self.seen:
            print("RETURNING BECAUSE ALREADY SEEN CODE HASH: ", hash(code))
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


BaseProcess = multiprocessing.process.BaseProcess
base_process_bootstrap = BaseProcess._bootstrap
base_process_start = BaseProcess.start
base_process_init = BaseProcess.__init__
base_process_run = BaseProcess.run
base_process_close = BaseProcess.close
base_process_join = BaseProcess.join
base_process_terminate = BaseProcess.terminate
base_process_kill = BaseProcess.kill

class CoverageCollectingMultiprocess(BaseProcess):

    def _consume_coverage_queue(self):
        if ModuleCodeCollector._instance is None:
            return

        for _ in range(self._dd_coverage_queue.qsize()):
            path, line = self._dd_coverage_queue.get()
            print(f"ROMAIN PROCESSING QUEUE ITEM {os.getpid()=}, {path=}, {line=}")
            ModuleCodeCollector._instance.hook((path, line))
    def _bootstrap(self, *args, **kwargs):
        print(f"ROMAIN PATCHED BOOTSTRAP {os.getpid()=}")
        print(f"ROMAIN BOOTSTRAP IS INSTALLING {ModuleCodeCollector.is_installed()=} IN PROCESS {os.getpid()=}")
        print(f"ROMAIN BOOTSTRAP ATTRS {self._dd_coverage_enabled=}, {self._dd_coverage_include_paths=}, {self._dd_coverage_queue=}")

        # Install the module code collector
        if self._dd_coverage_enabed:
            print(f"ROMAIN BOOTSTRAP IS ENABLING COVERAGE {os.getpid()=}, {self._dd_coverage_include_paths=}, {self._dd_coverage_queue=}")
            ModuleCodeCollector.install(coverage_queue=self._dd_coverage_queue, include_paths=self._dd_coverage_include_paths)
            ModuleCodeCollector.start_coverage()

        print(f"ROMAIN BOOTSTRAP IS INSTALLED {ModuleCodeCollector.is_installed()=} IN PROCESS {os.getpid()=}")

        # Call the original bootstrap method
        rval = base_process_bootstrap(self, *args, **kwargs)
        print(f"ROMAIN PATCHED BOOTSTRAP DONE {os.getpid()=}")
        return rval

    def __init__(self, *posargs, **kwargs):
        print(f"ROMAIN PATCHED INIT CALLED {os.getpid()=}, {posargs=}, {kwargs=}, {self.__class__=}")
        print(f"ROMAIN CURRENT COLLECTOR {ModuleCodeCollector.coverage_enabled()=}")
        self._dd_coverage_enabled = False
        self._dd_coverage_queue = None
        self._dd_coverage_include_paths = []
        if ModuleCodeCollector.coverage_enabled():
            self._dd_coverage_enabled = True
            self._dd_coverage_queue = multiprocessing.Queue()
            self._dd_coverage_include_paths = ModuleCodeCollector._instance._include_paths
        base_process_init(self, *posargs, **kwargs)
        print(f"ROMAIN PATCHED INIT COMPLETE {os.getpid()=}, {posargs=}, {kwargs=}, {self.__class__=}")

    def run(self, *args, **kwargs):
        print(f"ROMAIN PATCHED CALLING RUN {os.getpid()=}, {args=}, {kwargs=}")
        rval = base_process_run(self)
        print(f"ROMAIN PATCHED CALLED RUN {os.getpid()=}, {args=}, {kwargs=}")
        return rval

    def start(self, *args, **kwargs):
        print(f"ROMAIN PATCHED START CALLED {os.getpid()=}, {args=}, {kwargs=}")
        rval = base_process_start(self)
        print(f"ROMAIN PATCHED START COMPLETED {os.getpid()=}, {args=}, {kwargs=}")
        return rval
    def join(self, *args, **kwargs):
        print(f"ROMAIN PATCHED CALLING JOIN {os.getpid()=}, {args=}, {kwargs=}")
        rval = base_process_join(self, *args, **kwargs)
        print(f"ROMAIN PATCHED CALLED JOIN {os.getpid()=}, {args=}, {kwargs=}")
        return rval
    def close(self):
        print(f"ROMAIN PATCHED CALLING CLOSE {os.getpid()=}")
        rval = base_process_close(self)
        print(f"ROMAIN PATCHED CALLED CLOSE {os.getpid()=}")
        return rval
    def terminate(self):
        print(f"ROMAIN PATCHED CALLING TERMINATE {os.getpid()=}")
        rval = base_process_terminate(self)
        print(f"ROMAIN PATCHED CALLED TERMINATE {os.getpid()=}")
        return rval
    def kill(self):
        print(f"ROMAIN PATCHED CALLING KILL {os.getpid()=}")
        rval = base_process_kill(self)
        print(f"ROMAIN PATCHED CALLED KILL {os.getpid()=}")
        return rval

def _patch_multiprocessing():
    print("ROMAIN PATCHING")
    if hasattr(multiprocessing, "datadog_patch"):
        print("ROMAIN ALREADY PATCHED")

        return
    multiprocessing.process.BaseProcess._bootstrap = CoverageCollectingMultiprocess._bootstrap
    multiprocessing.process.BaseProcess.start = CoverageCollectingMultiprocess.start
    multiprocessing.process.BaseProcess.__init__ = CoverageCollectingMultiprocess.__init__
    multiprocessing.process.BaseProcess.run = CoverageCollectingMultiprocess.run
    multiprocessing.process.BaseProcess.close = CoverageCollectingMultiprocess.close
    multiprocessing.process.BaseProcess.terminate = CoverageCollectingMultiprocess.terminate
    multiprocessing.process.BaseProcess.kill = CoverageCollectingMultiprocess.kill

    print("ROMAIN PATCHED")
    setattr(multiprocessing, "datadog_patch", True)
