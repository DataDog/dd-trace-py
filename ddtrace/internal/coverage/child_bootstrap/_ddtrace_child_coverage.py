"""Records the lines a Python child process of a test runs, for native per-test line coverage.

A test process that follows its child processes puts this directory first on PYTHONPATH and
names its state directory in _DD_COVERAGE_CHILD_DIR, so every Python process it starts imports
the sitecustomize next to this file at start-up, which calls start() on CPython 3.12+. The child
loads the native collector by path, records every line it runs in the workspace, initialisers
included, since the whole child runs for the context that started it, and at exit writes them
to a record file. The test process merges the record into that context.

A process forked from the test process or from a child records the lines it runs from the fork
on, for the context that ran the fork (start_forked_record()): the lines run before the fork are
in its parent's record. It writes them however it ends: at exit, through os._exit(), and on
SIGTERM or SIGHUP unless the signal is ignored. A thread that ends the process while another
writes the record waits for it, up to two seconds.

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

Each record holds the lines run since the process last wrote one, so a process that writes more
than one, as when a signal handler lets it go on, reports each line once.

It imports no ddtrace module, so a child pays only for the collector, and any failure leaves the
child unrecorded and otherwise unchanged. Not recorded: interpreters started with -E, -I or -S,
which skip sitecustomize; another Python version or build than the test process's; a child that
is not forked and exits through os._exit() or a signal, except a multiprocessing child that
SIGTERM ends once its run started and a spawn child that SIGTERM ends while it unpickles its
process, unless its run replaced the SIGTERM handler without calling the one before; a forked
process that calls an os._exit() bound before following started, or that a signal handler set
after its fork ends without calling the one before; what a forked process runs in the exit
handlers registered before its fork; a process that a multiprocessing fork server forks, outside
its run; children still running when the test session ends; a file outside the workspace that
is a link to a file in it.
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
# How long a thread that ends a forked process waits for another thread's write, in 1 ms steps.
_WAIT_FOR_WRITE_STEPS = 2000
# The token of the context this process records for. A multiprocessing child takes the one its
# Process object names (run_process()).
_owner = 0
# What a spawn child writes its record with when SIGTERM ends it while it unpickles its process.
_startup_record: _ProcessRecord | None = None
# The process start() ran in. The processes it forks inherit its exit handler, and write records
# of their own.
_start_pid = 0
# The record of this process when it is a forked process.
_forked: _ForkedRecord | None = None
# The handlers that _on_signal passes SIGTERM and SIGHUP on to, in a forked process.
_signal_handlers: dict[int, Any] = {}
# The os._exit that _exit_with_record replaced.
_original_exit: Any = None


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
    """Write the lines run since the last record, for the context owner names, and forget them."""
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
        linecov.forget()
        # A process forked from the test process does not map the state file; mapping it again
        # where it is mapped costs a few system calls.
        linecov.share_state(directory + "/state")
        linecov.add_record()
    except Exception:  # nosec: the test process may have ended and removed the directory
        pass


def _write_final_record(linecov: Any, directory: str) -> None:
    import posix

    if posix.getpid() == _start_pid:
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
            # A handler that returns lets the run go on: the lines it runs from then on are written
            # when the run ends.
            previous(signum, frame)
            return
        import posix
        import signal

        signal.signal(signum, signal.SIG_DFL)
        posix.kill(posix.getpid(), signum)


class _ForkedRecord:
    """Writes the record of a forked process, for the context that ran the fork."""

    __slots__ = ("linecov", "directory", "owner", "pid", "writer", "pending")

    def __init__(self, linecov: Any, directory: str, owner: int) -> None:
        import posix

        self.linecov = linecov
        self.directory = directory
        self.owner = owner
        self.pid = posix.getpid()
        # The thread writing the record, or 0.
        self.writer = 0
        # A signal that arrived on that thread while it wrote, as (signum, frame).
        self.pending: Any = None

    def write(self) -> None:
        import _thread
        import posix

        # A process forked from this one inherits the record, and starts one of its own.
        if posix.getpid() != self.pid:
            return
        thread = _thread.get_ident()
        if self.writer == thread:
            # Called from a signal handler that interrupted the write.
            return
        if self.writer:
            # Ending the process now would lose the record another thread is writing.
            import time

            for _ in range(_WAIT_FOR_WRITE_STEPS):
                if not self.writer:
                    break
                time.sleep(0.001)
        self.writer = thread
        try:
            _write_record(self.linecov, self.directory, self.owner)
        finally:
            self.writer = 0
        pending = self.pending
        if pending is not None:
            self.pending = None
            _pass_on_signal(*pending)


def has_forked_record() -> bool:
    """Whether this process is a forked process that start_forked_record() records."""
    import posix

    record = _forked
    return record is not None and record.pid == posix.getpid()


def _write_forked_record() -> None:
    record = _forked
    if record is not None:
        record.write()


def _on_signal(signum: int, frame: Any) -> None:
    """Write the record of this forked process, then handle the signal as before."""
    import _thread

    record = _forked
    if record is not None:
        if record.writer == _thread.get_ident():
            # Handled once the record is written.
            record.pending = (signum, frame)
            return
        record.write()
    _pass_on_signal(signum, frame)


def _pass_on_signal(signum: int, frame: Any) -> None:
    previous = _signal_handlers.get(signum)
    if callable(previous):
        # A handler that returns lets the process go on: the lines it runs from then on are
        # written when it ends.
        previous(signum, frame)
        return
    import _signal
    import posix

    _signal.signal(signum, _signal.SIG_DFL)
    posix.kill(posix.getpid(), signum)


def _exit_with_record(status: int) -> None:
    """os._exit(), once the record of a forked process is written."""
    record = _forked
    if record is not None:
        record.write()
    _original_exit(status)


def wrap_exit() -> None:
    """Make os._exit() write the record of a forked process first."""
    global _original_exit

    if os._exit is not _exit_with_record:
        _original_exit = os._exit
        os._exit = _exit_with_record  # type: ignore[assignment]


def unwrap_exit() -> None:
    # Whoever replaced it since keeps theirs, which calls the wrapper.
    if os._exit is _exit_with_record:
        os._exit = _original_exit


def start_forked_record(linecov: Any, directory: str, owner: int) -> None:
    """Record the lines this forked process runs from now on, for the context owner names.

    The lines its parent ran before the fork are its parent's to report. The record is written at
    exit, through os._exit() once wrap_exit() ran, and on SIGTERM or SIGHUP unless the signal is
    ignored or handled outside Python.
    """
    global _forked, _owner

    linecov.forget()
    _owner = owner
    # The children of this process run for the same context.
    os.environ[_OWNER_ENV] = str(owner)
    _forked = _ForkedRecord(linecov, directory, owner)
    import _signal
    import atexit

    # Registered at the fork, as the last handler: the handlers registered after the fork run
    # before it, and the record holds what they run.
    atexit.unregister(_write_forked_record)
    atexit.register(_write_forked_record)
    for signum in (_signal.SIGTERM, _signal.SIGHUP):
        try:
            handler = _signal.getsignal(signum)
            # Inherited from the parent, with the handler it passes the signal on to.
            if handler is _on_signal:
                continue
            # An ignored signal ends nothing, and a handler set outside Python stays.
            if handler == _signal.SIG_DFL or callable(handler):
                _signal_handlers[signum] = handler
                _signal.signal(signum, _on_signal)
        except Exception:  # nosec: not the main thread
            pass


def _follow_forks(linecov: Any, directory: str) -> None:
    """Give each process this one forks a record of its own (start_forked_record())."""

    def after_fork_in_child() -> None:
        try:
            start_forked_record(linecov, directory, _owner)
        except Exception:  # nosec: the forked process stays unrecorded and otherwise unchanged
            pass

    # Not through ddtrace.internal.forksafe, which would import ddtrace into every child: this
    # process runs no ddtrace hook or thread that this hook would have to be ordered with.
    os.register_at_fork(after_in_child=after_fork_in_child)
    wrap_exit()


def run_process(linecov: Any, directory: str, owner: int, bootstrap: Any, process: Any, args: Any, kwargs: Any) -> Any:
    """Run a multiprocessing child's BaseProcess._bootstrap, for the context owner names, and write
    the record of its lines when the run ends or when SIGTERM arrives.
    """
    global _owner, _startup_record

    _owner = owner
    # The children of this child run for the same context.
    os.environ[_OWNER_ENV] = str(owner)
    forked = _forked
    if forked is not None and has_forked_record():
        # So does the record an os._exit() in the run writes.
        forked.owner = owner
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

    A child forked from this process forgets the lines this process ran, which are not its own,
    unless its fork started a record of its own (start_forked_record()).
    """
    # multiprocessing imports it next anyway.
    from multiprocessing.process import BaseProcess
    import posix

    bootstrap = BaseProcess._bootstrap  # type: ignore[attr-defined]
    pid = posix.getpid()

    def _bootstrap(self: Any, *args: Any, **kwargs: Any) -> Any:
        if posix.getpid() != pid and not has_forked_record():
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
    global _owner, _start_pid

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
    # A fork server forks for processes started in other contexts than the one it started in, which
    # their Process objects name (_cover_processes()).
    if start_method != "forkserver":
        _follow_forks(linecov, directory)

    import atexit
    import posix

    _start_pid = posix.getpid()
    # Registered before any handler the child's program adds, so it runs after them: the record
    # includes what they run.
    atexit.register(_write_final_record, linecov, directory)
