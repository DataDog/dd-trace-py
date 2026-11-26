"""Records the lines a Python child process of a test runs, for native per-test line coverage.

A test process that follows its child processes puts this directory first on PYTHONPATH and
names its state directory in _DD_COVERAGE_CHILD_DIR, so every Python process it starts imports
the sitecustomize next to this file at start-up, which calls start() on CPython 3.12+. The child
loads the native collector by path, records every line it runs in the workspace, initialisers
included, since the whole child runs for the context that started it, and at exit writes them
to a record file. The test process merges the record into that context.

A multiprocessing child writes its record when its run ends instead, at the end of
BaseProcess._bootstrap, for the context that started the process (run_process()): fork and
forkserver children leave through os._exit(), which skips atexit, and Pool.terminate() ends its
workers with SIGTERM. SIGTERM can also end a spawn child before its run starts, while it
unpickles its process, which imports the modules the target needs: the child then writes its
record for the context named at its start (start()), as its Process object is not unpickled yet.
The handler that writes the record is set when the unpickling starts, after the child's start-up
hooks (sitecustomize, usercustomize): a hook that handles SIGTERM only while the signal has its
default action still does, and the record's handler passes the signal on to it. Code that the
unpickling or the run imports or runs finds the record's handler in place instead.

It imports no ddtrace module, so a child pays only for the collector, and any failure leaves the
child unrecorded and otherwise unchanged. Not recorded: interpreters started with -E, -I or -S,
which skip sitecustomize; another Python version or build than the test process's; children
that exit through os._exit() or a signal, which skip the record, except a multiprocessing child
that SIGTERM ends once its run started and a spawn child that SIGTERM ends while it unpickles its
process, unless its run replaced the SIGTERM handler without calling the one before; children
still running when the test session ends; a file outside the workspace that is a link to a file
in it.
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
# The token of the context this process records for. A multiprocessing child takes the one its
# Process object names (run_process()).
_owner = 0
# What a spawn child writes its record with when SIGTERM ends it while it unpickles its process.
_startup_record: _ProcessRecord | None = None


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
        # A process forked from the test process does not map the state file; mapping it again
        # where it is mapped costs a few system calls.
        linecov.share_state(directory + "/state")
        linecov.add_record()
    except Exception:  # nosec: the test process may have ended and removed the directory
        pass


def _write_final_record(linecov: Any, directory: str) -> None:
    _write_record(linecov, directory, _owner)


class _ProcessRecord:
    """Writes the record of a multiprocessing child when its run ends, or when SIGTERM arrives."""

    __slots__ = ("linecov", "directory", "owner", "previous", "writing", "pending", "replaced")

    def __init__(self, linecov: Any, directory: str, owner: int) -> None:
        self.linecov = linecov
        self.directory = directory
        self.owner = owner
        # The SIGTERM handler in place before on_sigterm: SIG_DFL or a Python function.
        self.previous: Any = None
        self.writing = False
        # A SIGTERM that arrived during write(), as (signum, frame).
        self.pending: Any = None
        # Set once another record handles SIGTERM instead: on_sigterm then only passes it on.
        self.replaced = False

    def handle_sigterm(self, replacing: _ProcessRecord | None = None) -> None:
        """Write the record when SIGTERM arrives, then handle the signal as before.

        A record passed as replacing writes nothing from then on. If its handler is still in place,
        this record's handler takes over from it and passes the signal on to the one it had replaced.
        """
        try:
            import signal

            previous = signal.getsignal(signal.SIGTERM)
            if replacing is not None and previous == replacing.on_sigterm:
                previous = replacing.previous
            # An ignored signal ends nothing, and a handler set outside Python stays.
            if previous is signal.SIG_DFL or callable(previous):
                self.previous = previous
                signal.signal(signal.SIGTERM, self.on_sigterm)
        except Exception:  # nosec: not the main thread
            pass
        if replacing is not None:
            replacing.replaced = True
            # Passed on when the run ends, like a SIGTERM that arrives while this record is written.
            self.pending = replacing.pending

    def write(self) -> None:
        self.writing = True
        try:
            _write_record(self.linecov, self.directory, self.owner)
        finally:
            self.writing = False

    def on_sigterm(self, signum: int, frame: Any) -> None:
        if self.writing:
            # Handled once the record is written.
            self.pending = (signum, frame)
            return
        if not self.replaced:
            self.write()
        self.pass_on(signum, frame)

    def pass_on(self, signum: int, frame: Any) -> None:
        """Handle the signal as the handler before on_sigterm would have."""
        previous = self.previous
        if callable(previous):
            # A handler that returns lets the run go on: the record written at its end is a
            # superset of this one.
            previous(signum, frame)
            return
        import posix
        import signal

        signal.signal(signum, signal.SIG_DFL)
        posix.kill(posix.getpid(), signum)


def run_process(linecov: Any, directory: str, owner: int, bootstrap: Any, process: Any, args: Any, kwargs: Any) -> Any:
    """Run a multiprocessing child's BaseProcess._bootstrap, for the context owner names, and write
    the record of its lines when the run ends or when SIGTERM arrives.
    """
    global _owner, _startup_record

    _owner = owner
    # The children of this child run for the same context.
    os.environ[_OWNER_ENV] = str(owner)
    record = _ProcessRecord(linecov, directory, owner)
    record.handle_sigterm(_startup_record)
    _startup_record = None
    try:
        return bootstrap(process, *args, **kwargs)
    finally:
        record.write()
        # A spawn child runs atexit handlers next: their lines make a record of their own.
        linecov.forget()
        pending = record.pending
        if pending is not None:
            record.pending = None
            record.pass_on(*pending)


def _start_method(argv: list[str]) -> str:
    """The multiprocessing start method that started this interpreter, to run a process ("spawn") or
    to fork them ("forkserver"), or "" if multiprocessing did not start it.
    """
    try:
        command = argv[argv.index("-c") + 1]
    except (ValueError, IndexError):
        return ""
    if "from multiprocessing.spawn import spawn_main" in command:
        return "spawn"
    # Python 3.13 and later put "import sys; " before the forkserver's import.
    if "from multiprocessing.forkserver import main" in command:
        return "forkserver"
    return ""


def _cover_processes(linecov: Any, directory: str) -> None:
    """Wrap BaseProcess._bootstrap, which a spawn child and each child a forkserver forks run.

    A child forked from this process forgets the lines this process ran, which are not its own.
    """
    # multiprocessing imports it next anyway.
    from multiprocessing.process import BaseProcess
    import posix

    bootstrap = BaseProcess._bootstrap  # type: ignore[attr-defined]
    pid = posix.getpid()

    def _bootstrap(self: Any, *args: Any, **kwargs: Any) -> Any:
        if posix.getpid() != pid:
            linecov.forget()
        # The test process names the context on the Process object; a process started here runs
        # for this process's context.
        record = getattr(self, "_dd_coverage_record", None)
        owner = record[1] if record is not None else _owner
        return run_process(linecov, directory, owner, bootstrap, self, args, kwargs)

    BaseProcess._bootstrap = _bootstrap  # type: ignore[attr-defined]


def _cover_unpickling(linecov: Any, directory: str) -> None:
    """Wrap multiprocessing.spawn._main, which unpickles the process a spawn child runs, so that a
    SIGTERM from then on writes the record: Process.terminate() or Pool.terminate() soon after the
    start can end the child there, before its run.
    """
    # The child's command imports it next anyway.
    from multiprocessing import spawn

    main = spawn._main

    def _main(*args: Any, **kwargs: Any) -> Any:
        global _startup_record

        _startup_record = _ProcessRecord(linecov, directory, _owner)
        _startup_record.handle_sigterm()
        return main(*args, **kwargs)

    spawn._main = _main


def start() -> None:
    """Record this process's lines for the context named in its environment or the state file."""
    global _owner

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
    _owner = int(named_owner) if named_owner else int(linecov.owner())
    # The children of this child run for the same context, even once the test process runs another.
    os.environ[_OWNER_ENV] = str(_owner)
    linecov.add_exec_hook(_exec_hook(linecov, config))
    start_method = _start_method(sys.orig_argv)
    if start_method:
        _cover_processes(linecov, directory)
    if start_method == "spawn":
        _cover_unpickling(linecov, directory)

    import atexit

    # Registered before any handler the child's program adds, so it runs after them: the record
    # includes what they run.
    atexit.register(_write_final_record, linecov, directory)
