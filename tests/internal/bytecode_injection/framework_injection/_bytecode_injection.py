import atexit
import dis
import sys
import types
from types import ModuleType
import typing as t

from _config import config
from output import log
from utils import COLS
from utils import CWD
from utils import from_editable_install
from utils import is_ddtrace
from utils import is_included

from ddtrace.internal.bytecode_injection.core import CallbackType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.compat import Path
from ddtrace.internal.module import BaseModuleWatchdog


INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, staticmethod, type)
_callback_lines: t.Dict[str, set[int]] = {}
_tracked_lines: t.Dict[str, t.List[int]] = {}


def instrument_all_lines(func, callback: CallbackType) -> t.List[int]:
    code_to_instr = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    if "__code__" not in dir(code_to_instr):
        return []
    code = code_to_instr.__code__

    injection_context = InjectionContext(
        code, callback, lambda _s: [o for o, _ in dis.findlinestarts(_s.original_code)]
    )
    new_code, seen_lines = inject_invocation(injection_context, code.co_filename, "package.py")

    code_to_instr.__code__ = new_code
    return seen_lines


def line_callback(*args):
    cb_info = args[0]
    line = cb_info[0]
    file_path = cb_info[1]
    global _callback_lines
    if file_path not in _callback_lines:
        _callback_lines[file_path] = set()
    _callback_lines[file_path].add(line)


class InjectionWatchdog(BaseModuleWatchdog):
    def __init__(self, *args, **kwargs):
        super(InjectionWatchdog, self).__init__(*args, **kwargs)

        self._imported_modules: t.Set[str] = set()
        self._instrumented_modules: t.Set[str] = set()

        atexit.register(self.report_callback_called)

    def _on_new_module(self, module):
        try:
            if not is_ddtrace(module):
                if config.include:
                    if not is_included(module, config):
                        return
                elif not from_editable_install(module, config):
                    # We want to instrument only the modules that belong to the
                    # codebase and exclude the modules that belong to the tests
                    # and the dependencies installed within the virtual env.
                    return

                try:
                    self._instrument_module(module.__name__)
                except Exception as e:
                    status("Error collecting functions from %s: %s" % (module.__name__, e))
                    raise e

            status("Excluding module %s" % module.__name__)

        except Exception as e:
            status("Error after module import %s: %s" % (module.__name__, e))
            raise e

    def after_import(self, module: ModuleType):
        name = module.__name__
        if name in self._imported_modules:
            return

        self._imported_modules.add(name)

        self._on_new_module(module)

        if config.elusive:
            # Handle any new modules that have been imported since the last time
            # and that have eluded the import hook.
            for m in list(_ for _ in sys.modules.values() if _ is not None):
                # In Python 3 we can check if a module has been fully
                # initialised. At this stage we want to skip anything that is
                # only partially initialised.
                try:
                    if m.__spec__._initializing:
                        continue
                except AttributeError:
                    continue

                name = m.__name__
                if name not in self._imported_modules:
                    self._imported_modules.add(name)
                    self._on_new_module(m)

    def report_callback_called(self) -> None:
        global _tracked_lines
        global _callback_lines

        _tracked_lines_path: t.Dict[Path, t.List[int]] = {Path(k).resolve(): v for k, v in _tracked_lines.items()}
        try:
            w = max(len(str(o.relative_to(CWD))) for o in _tracked_lines_path)
        except ValueError:
            w = int(COLS * 0.75)

        log(("{:=^%ds}" % COLS).format(" Bytecode Injection Coverage "))
        log("")
        head = ("{:<%d} {:>5} {:>6} {:>7}" % w).format("Source", "Lines", "Covered", "Diff")
        log(head)
        log("=" * len(head))

        total_lines = 0
        total_covered = 0
        total_diff = 0
        for o, lines in sorted(_tracked_lines.items(), key=lambda x: x[0]):
            total_lines += len(lines)
            seen_lines = []
            if o in _callback_lines:
                seen_lines = _callback_lines[o]
            total_covered += len(seen_lines)

            # we want to be sure we instrument the same lines
            lines_instrumented = set(lines)
            lines_covered = set(seen_lines)
            diff = (lines_instrumented - lines_covered) | (lines_covered - lines_instrumented)
            total_diff += len(diff)
            try:
                path = str(Path(o).resolve().relative_to(CWD))
            except Exception:
                path = ""
            log(
                ("{:<%d} {:>5} {: 6.0f}%% {:>7}" % w).format(
                    path,
                    len(lines),
                    len(seen_lines) * 100.0 / len(lines) if lines else 0,
                    str(sorted(diff)),
                )
            )
        if not total_lines:
            log("No lines found")
            return
        log("-" * len(head))
        log(
            ("{:<%d} {:>5} {: 6.0f}%% {:>7}" % w).format(
                "TOTAL", total_lines, total_covered * 100.0 / total_lines, total_diff
            )
        )
        log("")

    def _instrument_module(self, module_name: str):
        if module_name in self._instrumented_modules:
            return
        self._instrumented_modules.add(module_name)

        mod = sys.modules[module_name]
        names = dir(mod)

        for name in names:
            if name in mod.__dict__:
                obj = mod.__dict__[name]
                if (
                    type(obj) in INSTRUMENTABLE_TYPES
                    and (module_name == "__main__" or obj.__module__ == module_name)
                    and not name.startswith("__")
                ):
                    self._instrument_obj(obj)

    def _instrument_obj(self, obj):
        if (
            type(obj) in (types.FunctionType, types.MethodType, staticmethod)
            and hasattr(obj, "__name__")
            and not self._is_reserved(obj.__name__)
        ):
            global _tracked_lines
            nb_instrumented_lines = instrument_all_lines(obj, callback=line_callback)
            if len(nb_instrumented_lines) != 0:
                if hasattr(obj, "__code__") is False:
                    filename = obj.__wrapped__.__code__.co_filename
                else:
                    filename = obj.__code__.co_filename
                if filename not in _tracked_lines:
                    _tracked_lines[filename] = []
                _tracked_lines[filename].extend(nb_instrumented_lines)
        elif isinstance(obj, type):
            # classes
            for candidate in obj.__dict__.keys():
                if type(obj) in INSTRUMENTABLE_TYPES and not self._is_reserved(candidate):
                    self._instrument_obj(obj.__dict__[candidate])

    def _is_reserved(self, name: str) -> bool:
        return name.startswith("__") and name != "__call__"


if config.status_messages:

    def status(msg: str) -> None:
        log(("{:%d}" % COLS).format(msg))

else:

    def status(msg: str) -> None:
        pass


InjectionWatchdog.install()
