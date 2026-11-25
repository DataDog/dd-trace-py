"""Records the lines a Python child process of a test runs, for native per-test line coverage.

A test process that follows its child processes puts this directory first on PYTHONPATH and
names its state directory in _DD_COVERAGE_CHILD_DIR, so every Python process it starts imports
the sitecustomize next to this file at start-up, which calls start() on CPython 3.12+. The child
loads the native collector by path, records every line it runs in the workspace, initialisers
included, since the whole child runs for the context that started it, and at exit writes them
to a record file. The test process merges the record into that context.

It imports no ddtrace module, so a child pays only for the collector, and any failure leaves the
child unrecorded and otherwise unchanged. Not recorded: interpreters started with -E, -I or -S,
which skip sitecustomize; another Python version or build than the test process's; children
that exit through os._exit() or a signal, which skip the record; children still running when the
test session ends; a file outside the workspace that is a link to a file in it.
"""

from __future__ import annotations

import os
import sys


# Read as true by type checkers only: importing typing would cost every child a few milliseconds.
TYPE_CHECKING = False
if TYPE_CHECKING:
    from types import CodeType
    from types import ModuleType
    from typing import Any
    from typing import Callable


_DIR_ENV = "_DD_COVERAGE_CHILD_DIR"
_OWNER_ENV = "_DD_COVERAGE_CHILD_OWNER"
# The state file starts with the counters the test process maps; the configuration follows.
_STATE_HEADER_SIZE = 64
_TOOL_IDS = (4, 3, 1)
_SITE_PACKAGES = frozenset(("site-packages", "dist-packages"))


def _read_config(directory: str) -> dict[str, Any]:
    with open(os.path.join(directory, "state"), "rb") as f:
        data = f.read()
    config: dict[str, Any] = {"include": [], "exclude": []}
    for line in data[_STATE_HEADER_SIZE:].decode("utf-8").splitlines():
        key, _, value = line.partition("=")
        if key in ("include", "exclude"):
            config[key].append(value)
        elif key:
            config[key] = value
    return config


class _Spec:
    """The two attributes of a module spec that loading an extension module reads."""

    __slots__ = ("name", "origin")

    def __init__(self, name: str, origin: str) -> None:
        self.name = name
        self.origin = origin


def _load_collector(path: str) -> ModuleType:
    # _imp loads the extension without importing importlib.machinery, which a child that does
    # not run a module with -m never imports.
    import _imp

    module = _imp.create_dynamic(_Spec("ddtrace.internal.coverage._linecov", path))  # type: ignore[arg-type]
    # Without its parent packages in sys.modules, the entry would only confuse a later import.
    sys.modules.pop(module.__name__, None)
    return module


def _is_under(path: str, top: str) -> bool:
    return path == top or path.startswith(top if top.endswith(os.sep) else top + os.sep)


def _exec_hook(linecov: Any, config: dict[str, Any]) -> Callable[[CodeType], None]:
    """The function the collector calls with each code object exec() is about to run.

    It mirrors ModuleCodeCollector._is_instrumentable_file on the resolved path, but decides once
    per directory: a child imports hundreds of files from a few dozen directories. Only the files
    of a workspace directory are checked for being links themselves.
    """
    # The posix module rather than os, which the child's program may patch (pyfakefs, mock).
    import posix
    import stat

    root: str = config["root"]
    root_prefix = root if root.endswith(os.sep) else root + os.sep
    includes: list[str] = config["include"]
    excludes: list[str] = config["exclude"]
    file_ids: dict[str, int] = {}
    # Each directory a file was run from, mapped to its resolved path when its files are
    # instrumentable, else to None.
    directories: dict[str, str | None] = {}

    def resolve(path: str) -> str:
        resolved: str | None = linecov.realpath(path)
        return os.path.realpath(path) if resolved is None else resolved

    def is_link(path: str) -> bool:
        try:
            return stat.S_ISLNK(posix.lstat(path).st_mode)
        except (OSError, ValueError):
            return False

    def is_instrumentable(path: str) -> bool:
        if not _SITE_PACKAGES.isdisjoint(path.split(os.sep)):
            return False
        return any(_is_under(path, top) for top in includes) and not any(_is_under(path, top) for top in excludes)

    def file_id(filename: str) -> int:
        # Python source files only, as in the test process: template engines such as Jinja compile
        # code named after the template file. Children are followed on POSIX, where that means .py.
        if not filename.endswith(".py") or filename.startswith("<"):
            return -1
        if os.path.isabs(filename):
            directory, _, name = filename.rpartition(os.sep)
            if directory in directories:
                resolved = directories[directory]
            else:
                resolved = resolve(directory or os.sep)
                resolved = directories[directory] = resolved if is_instrumentable(resolved) else None
            if resolved is None:
                return -1
            path = os.path.join(resolved, name)
            # The test process names files by their resolved path.
            if is_link(path):
                path = resolve(path)
                if not is_instrumentable(path):
                    return -1
        else:
            path = resolve(filename)
            if not is_instrumentable(path):
                return -1
        report_path = "/" + path[len(root_prefix) :].replace(os.sep, "/") if path.startswith(root_prefix) else None
        return int(linecov.register_file(path, report_path))

    def on_exec(code: CodeType) -> None:
        filename = code.co_filename
        found = file_ids.get(filename)
        if found is None:
            try:
                found = file_id(filename)
            except Exception:
                found = -1
            file_ids[filename] = found
        if found >= 0:
            linecov.instrument(code, found)

    return on_exec


def _write_record(linecov: Any, directory: str, owner: int) -> None:
    # Through the posix module: the child's program may have left the os functions and open patched
    # (pyfakefs, mock) by the time it exits.
    try:
        files = linecov.union()
        if not files:
            return
        import marshal
        import posix

        name = "%d-%d-%s" % (owner, posix.getpid(), posix.urandom(4).hex())
        temporary = directory + "/" + name + ".tmp"
        data = memoryview(marshal.dumps(files))
        fd = posix.open(temporary, posix.O_WRONLY | posix.O_CREAT | posix.O_TRUNC, 0o600)
        try:
            while data:
                data = data[posix.write(fd, data) :]
        finally:
            posix.close(fd)
        posix.replace(temporary, directory + "/" + name + ".rec")
        linecov.add_record()
    except Exception:  # nosec: the test process may have ended and removed the directory
        pass


def start() -> None:
    """Record this process's lines for the context named in its environment or the state file."""
    directory = os.environ.get(_DIR_ENV)
    if not directory or not hasattr(sys, "monitoring"):
        return
    config = _read_config(directory)
    # The opcode tables in the configuration hold for the test process's interpreter build only.
    if config.get("build") != sys.version.replace("\n", " "):
        return
    linecov: Any = _load_collector(config["extension"])
    monitoring = sys.monitoring
    tool_id = next((tool for tool in _TOOL_IDS if monitoring.get_tool(tool) is None), None)
    if tool_id is None:
        return
    monitoring.use_tool_id(tool_id, "datadog")

    # The built-in module, not contextvars, which a child would have to find on sys.path.
    from _contextvars import ContextVar

    events = monitoring.events
    linecov.init(
        tool_id=tool_id,
        line_event_index=events.LINE.bit_length() - 1,
        disable=monitoring.DISABLE,
        set_local_events=monitoring.set_local_events,
        # Never set: with no context, every line goes to the process-wide sets the record is made of.
        ctxvar=ContextVar("ddtrace_coverage_child_context", default=None),
        deopt=bytes.fromhex(config["deopt"]),
        instrumented_line=int(config["instrumented_line"]),
        enter_executor=int(config["enter_executor"]),
        instrumented_instruction=int(config["instrumented_instruction"]),
        internal_rearm=config.get("internal_rearm") == "1",
        instruction_event_index=events.INSTRUCTION.bit_length() - 1,
        min_instrumented=int(config["min_instrumented"]),
        superinstructions=bytes.fromhex(config["superinstructions"]),
        instruction_probes=config.get("instruction_probes") == "1",
        child=True,
    )
    monitoring.register_callback(tool_id, events.LINE, linecov.line_event)
    if linecov.HAVE_INSTRUCTION_PROBES:
        monitoring.register_callback(tool_id, events.INSTRUCTION, linecov.instruction_event)
    linecov.share_state(os.path.join(directory, "state"))
    named_owner = os.environ.get(_OWNER_ENV)
    owner = int(named_owner) if named_owner else int(linecov.owner())
    # The children of this child run for the same context, even once the test process runs another.
    os.environ[_OWNER_ENV] = str(owner)
    linecov.add_exec_hook(_exec_hook(linecov, config))

    import atexit

    # Registered before any handler the child's program adds, so it runs after them: the record
    # includes what they run.
    atexit.register(_write_record, linecov, directory, owner)
