"""Native per-test line coverage on sys.monitoring LINE events (CPython 3.12+).

The LINE callback is the C function _linecov.line_event. It credits each line to the context held
in ctx_native when that context is active (a test, or a suite-scope fixture setup), to the file's
initialiser lines when the code is a module or class body, and to the process's unowned lines
otherwise. See _linecov.c for the attribution and re-arm rules.

On CPython 3.12, a code object moves at its first LINE event to INSTRUCTION probes: one probe per
line start, or with _DD_COVERAGE_BLOCK_PROBES=true block probes, each of which credits a basic block
and the blocks that dominate it. Block probes fire fewer events but cost more to arm, and were no
faster on any repository measured. They also need PY_UNWIND events, and switch off for good once
greenlet is imported.

On by default where it can run (CPython 3.12+ builds with the GIL and this extension), and then coverage is
line-level. _DD_COVERAGE_NATIVE_LINES=false turns it off.
"""

import builtins
from contextvars import ContextVar
import dis
import marshal
import os
import sys
from types import CodeType
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal.coverage.child_bootstrap import _ddtrace_child_coverage as _child_coverage
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

try:
    from ddtrace.internal.coverage import _linecov  # type: ignore[attr-defined]
except ImportError:
    _linecov = None


# The native context that the current thread or task runs for. Threads, executor work items and
# asyncio tasks started from a context carry it; None means the work belongs to no test.
ctx_native: ContextVar[t.Optional[t.Any]] = ContextVar("ddtrace_coverage_native_context", default=None)

# sys.monitoring exists on 3.12+ only.
_monitoring: t.Any = getattr(sys, "monitoring", None)

_requested: bool = sys.version_info >= (3, 12) and asbool(env.get("_DD_COVERAGE_NATIVE_LINES", "true"))
_enabled: bool = False
_report_root: t.Optional[str] = None
_file_ids: dict[str, int] = {}
_exec_callback: t.Optional[t.Callable[[CodeType], t.Any]] = None
# builtins.exec as it was, and the wrapper that replaced it, while a callback is set.
_exec_wrapping: t.Optional[tuple[t.Any, t.Any]] = None


def requested() -> bool:
    """Whether native line collection was asked for and can be used on this interpreter."""
    return _requested and _linecov is not None and not _linecov.FREE_THREADED


def enabled() -> bool:
    return _enabled


def _all_opcodes() -> dict[str, int]:
    all_opmap: dict[str, int] = dict(getattr(dis, "_all_opmap", {}) or {})
    all_opmap.update(dis.opmap)
    all_opmap.update(getattr(dis, "_specialized_opmap", {}) or {})
    return all_opmap


def _deopt_table() -> bytes:
    """CPython's _PyOpcode_Deopt: each specialized opcode maps to its base opcode."""
    table = bytearray(range(256))
    all_opmap = _all_opcodes()
    deoptmap = getattr(dis, "deoptmap", None)
    if not deoptmap:
        deoptmap = {s: base for base, family in dis._specializations.items() for s in family}  # type: ignore[attr-defined]
    for specialized, base in deoptmap.items():
        if specialized in all_opmap and base in all_opmap:
            table[all_opmap[specialized]] = all_opmap[base]
    return bytes(table)


def _superinstruction_table() -> bytes:
    """1 for each CPython 3.12 superinstruction, such as LOAD_FAST__LOAD_FAST: the only opcode names with "__"."""
    table = bytearray(256)
    for name, op in _all_opcodes().items():
        if "__" in name and 0 <= op < 256:
            table[op] = 1
    return bytes(table)


def _min_instrumented_opcode() -> int:
    """CPython's MIN_INSTRUMENTED_OPCODE: the instrumented opcodes are the highest ones."""
    return min((op for name, op in dis.opmap.items() if name.startswith("INSTRUMENTED_")), default=256)


# What the block analysis needs to know of each CPython 3.12 base opcode: the BK_* kinds of _linecov.c.
_BLOCK_KINDS = {
    1: ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE", "POP_JUMP_IF_NONE", "POP_JUMP_IF_NOT_NONE"),
    2: ("JUMP_FORWARD",),
    3: ("JUMP_BACKWARD", "JUMP_BACKWARD_NO_INTERRUPT"),
    4: ("FOR_ITER",),
    5: ("SEND",),
    6: ("RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS"),
    7: ("RERAISE",),
    8: ("YIELD_VALUE",),
    9: ("EXTENDED_ARG",),
    10: ("PUSH_EXC_INFO",),
    11: ("CLEANUP_THROW", "END_ASYNC_FOR"),
    # What a handler that does not start with PUSH_EXC_INFO runs before its RERAISE.
    12: (
        "COPY",
        "POP_EXCEPT",
        "SWAP",
        "POP_TOP",
        "LOAD_CONST",
        "STORE_FAST",
        "STORE_NAME",
        "STORE_DEREF",
        "STORE_GLOBAL",
        "DELETE_FAST",
        "DELETE_NAME",
        "DELETE_DEREF",
        "DELETE_GLOBAL",
        "CALL_INTRINSIC_1",
        "NOP",
    ),
}
_BLOCK_UNKNOWN_JUMP = 13


def _block_tables() -> t.Optional[tuple[bytes, bytes]]:
    """The block kind and the inline cache entries of each base opcode, or None when this
    interpreter's opcodes are not CPython 3.12's.
    """
    cache_entries = getattr(dis, "_inline_cache_entries", None)
    if not isinstance(cache_entries, list) or len(cache_entries) != 256:
        return None
    kinds = bytearray(256)
    for kind, names in _BLOCK_KINDS.items():
        for name in names:
            op = dis.opmap.get(name)
            if op is None:
                return None
            kinds[op] = kind
    for op in set(dis.hasjrel) | set(dis.hasjabs):
        if 0 <= op < _min_instrumented_opcode() and kinds[op] == 0:
            kinds[op] = _BLOCK_UNKNOWN_JUMP
    return bytes(kinds), bytes(cache_entries)


def init(tool_id: int) -> bool:
    """Register the native callbacks for tool_id. Returns False when native collection is unusable."""
    global _enabled

    if not requested() or _linecov is None:
        return False
    block_probes = asbool(env.get("_DD_COVERAGE_BLOCK_PROBES", "false"))
    tables = _block_tables() if block_probes and _linecov.HAVE_BLOCK_PROBES else None
    try:
        events = _monitoring.events
        # Block probes start off: the first self-tests check the probes that block probes fall back on.
        internal = _linecov.init(
            tool_id=tool_id,
            line_event_index=events.LINE.bit_length() - 1,
            disable=_monitoring.DISABLE,
            set_local_events=_monitoring.set_local_events,
            ctxvar=ctx_native,
            deopt=_deopt_table(),
            instrumented_line=dis.opmap.get("INSTRUMENTED_LINE", -1),
            enter_executor=dis.opmap.get("ENTER_EXECUTOR", -1),
            instrumented_instruction=dis.opmap.get("INSTRUMENTED_INSTRUCTION", -1),
            internal_rearm=asbool(env.get("_DD_COVERAGE_INTERNAL_REARM", "true")),
            instruction_event_index=events.INSTRUCTION.bit_length() - 1,
            min_instrumented=_min_instrumented_opcode(),
            superinstructions=_superinstruction_table(),
            instruction_probes=asbool(env.get("_DD_COVERAGE_INSTRUCTION_PROBES", "true")),
            block_probes=False,
            block_kinds=tables[0] if tables else None,
            cache_entries=tables[1] if tables else None,
        )
        _monitoring.register_callback(tool_id, events.LINE, _linecov.line_event)
        if _linecov.HAVE_INSTRUCTION_PROBES:
            _monitoring.register_callback(tool_id, events.INSTRUCTION, _linecov.instruction_event)
    except Exception:
        log.debug("Native line coverage unavailable", exc_info=True)
        return False

    _enabled = True
    _apply_exec_callback()
    if internal and not _self_test(tool_id):
        _linecov.set_internal_rearm(False)
        log.debug("Internal re-arm self-test failed; using the public re-arm")
    elif _linecov.stats()["instruction_probes"] and not _self_test(tool_id, distant_lines=True):
        _linecov.set_instruction_probes(False)
        log.debug("INSTRUCTION probe self-test failed; using LINE events only")
    # Greenlets switch frames without a yield, which block probes cannot follow.
    if tables is not None and "greenlet" not in sys.modules and _start_block_probes(tool_id):
        ModuleWatchdog.after_module_imported("greenlet")(_stop_block_probes)
    return True


def _start_block_probes(tool_id: int) -> bool:
    """Turn block probes on, with the PY_UNWIND callback they need, if their self-test passes."""
    assert _linecov is not None  # nosec
    events = _monitoring.events
    _monitoring.register_callback(tool_id, events.PY_UNWIND, _linecov.unwind_event)
    _monitoring.set_events(tool_id, _monitoring.get_events(tool_id) | events.PY_UNWIND)
    if _linecov.set_block_probes(True) and _block_self_test(tool_id):
        return True
    _linecov.disable_blocks()
    _monitoring.set_events(tool_id, _monitoring.get_events(tool_id) & ~events.PY_UNWIND)
    _monitoring.register_callback(tool_id, events.PY_UNWIND, None)
    log.debug("Block probe self-test failed; using probes on every line start")
    return False


def _stop_block_probes(_module: t.Any) -> None:
    """Move every code object with block probes to probes on its line starts, for good. The PY_UNWIND
    callback stays registered, but finds no code object with block probes.
    """
    if _linecov is not None:
        _linecov.disable_blocks()
        log.debug("greenlet imported: block probes stopped")


_BLOCK_SELF_TEST_SOURCE = """\
def probe(n):
    total = 0
    for i in range(n):
        if i % 2:
            total += i
        else:
            total -= 1
    try:
        if n > 2:
            raise ValueError(n)
        total += 1
    except ValueError:
        total += 2
    return total
"""


def _block_self_test(tool_id: int) -> bool:
    """Check that block probes credit the lines LINE events do, for three contexts in turn.

    Two copies of a function with branches, a loop and a caught exception: the first decides on LINE
    events, the second on block probes, at their first LINE event.
    """
    assert _linecov is not None  # nosec
    filenames = ("<ddtrace-linecov-lines>", "<ddtrace-linecov-blocks>")
    functions = []
    for filename in filenames:
        namespace: dict[str, t.Any] = {}
        exec(compile(_BLOCK_SELF_TEST_SOURCE, filename, "exec"), namespace)  # nosec: B102 -- fixed probe source
        code = namespace["probe"].__code__
        _monitoring.set_local_events(tool_id, code, _monitoring.events.LINE)
        _linecov.attach(code, _linecov.register_file(filename, None))
        functions.append(namespace["probe"])
    codes = [f.__code__ for f in functions]
    instruction_probes = bool(_linecov.stats()["instruction_probes"])
    seen = []
    try:
        for k in range(3):
            context = _linecov.Context()
            previous = context.enter()
            try:
                if k == 0:
                    _linecov.set_block_probes(False)
                    _linecov.set_instruction_probes(False)
                    functions[0](3)
                    _linecov.set_instruction_probes(instruction_probes)
                    _linecov.set_block_probes(True)
                else:
                    functions[0](3)
                functions[1](3)
            finally:
                context.exit(previous)
            covered = context.covered()
            seen.append((covered.get(filenames[0]), covered.get(filenames[1])))
        probes = [_linecov.probes(code) for code in codes]
    finally:
        # Re-arm first: a code object left listed would get its events back at the next re-arm.
        _linecov.rearm()
        for code in codes:
            _monitoring.set_local_events(tool_id, code, 0)
    return probes == [1, 3] and all(lines is not None and lines == blocks for lines, blocks in seen)


def _self_test(tool_id: int, distant_lines: bool = False) -> bool:
    """Check that contexts re-armed internally see the same lines as the first one did.

    With distant_lines, a long docstring puts the body more than 127 lines past the def, where
    CPython 3.12 looks lines up in the line table, so the probe function must move to INSTRUCTION
    probes at its first LINE event.
    """
    assert _linecov is not None  # nosec
    docstring = "    '''" + "\n" * 300 + "    '''\n" if distant_lines else ""
    source = f"def probe(n):\n{docstring}    total = 0\n    for i in range(n):\n        total += i\n    return total\n"
    filename = "<ddtrace-linecov-probe>"
    namespace: dict[str, t.Any] = {}
    exec(compile(source, filename, "exec"), namespace)  # nosec: B102 -- fixed probe source
    code = namespace["probe"].__code__
    file_id = _linecov.register_file(filename, None)
    _monitoring.set_local_events(tool_id, code, _monitoring.events.LINE)
    _linecov.attach(code, file_id)
    seen = []
    previous = ctx_native.get()
    try:
        for _ in range(3):
            ctx = _linecov.Context()
            ctx.push()
            ctx_native.set(ctx)
            namespace["probe"](3)
            ctx_native.set(previous)
            ctx.pop()
            seen.append(ctx.covered().get(filename))
        expected_probes = 2 if distant_lines else 1
        probes_ok = not _linecov.HAVE_INSTRUCTION_PROBES or _linecov.probes(code) == expected_probes
    finally:
        ctx_native.set(previous)
        # Re-arm first: a code object left listed would get its events back at the next re-arm.
        _linecov.rearm()
        _monitoring.set_local_events(tool_id, code, 0)
    return (
        probes_ok
        and seen[0] is not None
        and seen[0] == seen[1] == seen[2]
        and bin(int.from_bytes(seen[0], "big")).count("1") == 4
    )


def set_report_root(path: str) -> None:
    global _report_root
    _report_root = path


def report_root() -> t.Optional[str]:
    return _report_root


def _report_path(path: str) -> t.Optional[str]:
    root = _report_root
    if root is None:
        return None
    # Both paths are canonical, so a prefix test is enough. os.path.relpath would call os.path.abspath,
    # which blocking-call detectors patch, and this runs for modules imported inside event loops.
    prefix = root if root.endswith(os.sep) else root + os.sep
    if len(path) <= len(prefix) or not path.startswith(prefix):
        return None
    return "/" + path[len(prefix) :].replace(os.sep, "/")


def _file_id(path: str) -> int:
    assert _linecov is not None  # nosec
    file_id = _file_ids.get(path)
    if file_id is None:
        file_id = _file_ids[path] = _linecov.register_file(path, _report_path(path))
    return file_id


def set_exec_callback(callback: t.Optional[t.Callable[[CodeType], t.Any]]) -> None:
    """Call callback with each code object exec() is about to run, once native collection is enabled.

    It sees the code that loaders compile and run themselves, outside the import hooks. Code already
    instrumented and code compiled from a string are not passed. builtins.exec is wrapped while a
    callback is set; None restores it.
    """
    global _exec_callback

    _exec_callback = callback
    if _enabled:
        _apply_exec_callback()


def _apply_exec_callback() -> None:
    global _exec_wrapping

    assert _linecov is not None  # nosec
    _linecov.set_exec_callback(_exec_callback)
    if _exec_callback is not None and _exec_wrapping is None:
        original = builtins.exec
        _exec_wrapping = (original, _linecov.wrap_exec(original))
        builtins.exec = _exec_wrapping[1]
    elif _exec_callback is None and _exec_wrapping is not None:
        original, wrapper = _exec_wrapping
        _exec_wrapping = None
        # Whoever replaced it since keeps theirs, which calls the wrapper, now without a callback.
        if builtins.exec is wrapper:
            builtins.exec = original


def instrument(code: CodeType, path: str) -> int:
    """Enable LINE events on a module's code tree, attached to its file. path is the canonical file path.

    The module body and the class bodies nested statically in it are marked as initialiser code.
    Obfuscated code objects, and the code nested in them, are left alone. Returns the number of
    code objects instrumented, or -1 when the module body itself looks obfuscated.
    """
    assert _linecov is not None  # nosec
    return t.cast(int, _linecov.instrument(code, _file_id(path)))


def new_context(fixture: bool = False) -> t.Any:
    assert _linecov is not None  # nosec
    return _linecov.Context(fixture)


def context_type() -> t.Any:
    """The type of a native context. Calling it makes a test context.

    A test context is also a window: a with statement runs the current thread and task in it, and
    merges the records of the child processes that exited before the window closes.
    """
    assert _linecov is not None  # nosec
    return _linecov.Context


def set_running(context: t.Any) -> None:
    """Run in context (a native context or None), which child processes started from now on inherit."""
    if _linecov is not None and _enabled:
        _linecov.set_running(context)
    else:
        ctx_native.set(context)


def shared() -> dict[str, list[tuple[str, bytes]]]:
    """The process's Shared record: initialiser, suite-setup and unowned lines, per file.

    The records of child processes that exited after their context's window closed are part of it.
    """
    if _linecov is None or not _enabled:
        return {}
    collect_children()
    return t.cast(dict[str, list[tuple[str, bytes]]], _linecov.shared())


# Child processes. The Python processes a context starts run on its behalf, so the lines they run
# are the context's. See child_bootstrap/_ddtrace_child_coverage.py for the child's side.
_CHILD_DIR_ENV = "_DD_COVERAGE_CHILD_DIR"
_CHILD_OWNER_ENV = "_DD_COVERAGE_CHILD_OWNER"
_BOOTSTRAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "child_bootstrap")
# The state file starts with the counters the collector maps; the children's configuration follows.
_STATE_HEADER_SIZE = 64
# Records are read through the posix module: when a window closes, the test may still have the os
# functions and open patched (pyfakefs, mock). Children are followed on POSIX systems only.
_posix: t.Any = sys.modules.get("posix")


class _ChildProcesses:
    __slots__ = ("directory", "records_seen", "python_path")

    def __init__(self, directory: str, python_path: t.Optional[str]) -> None:
        self.directory = directory
        self.records_seen = 0
        self.python_path = python_path


_children: t.Optional[_ChildProcesses] = None
# Popen._execute_child as it was before follow_children() wrapped it. Kept after following
# stops, since another wrapper may have wrapped ours in the meantime.
_original_execute_child: t.Any = None


def _children_config(include_paths: t.Sequence[str], exclude_paths: t.Sequence[str]) -> bytes:
    assert _linecov is not None and _report_root is not None  # nosec
    stats = _linecov.stats()
    lines = [
        "root=" + _report_root,
        "extension=" + _linecov.__file__,
        "build=" + sys.version.replace("\n", " "),
        "internal_rearm=%d" % stats["internal_rearm"],
        "instruction_probes=%d" % stats["instruction_probes"],
        "deopt=" + _deopt_table().hex(),
        "superinstructions=" + _superinstruction_table().hex(),
        "min_instrumented=%d" % _min_instrumented_opcode(),
        "instrumented_line=%d" % dis.opmap.get("INSTRUMENTED_LINE", -1),
        "instrumented_instruction=%d" % dis.opmap.get("INSTRUMENTED_INSTRUCTION", -1),
        "enter_executor=%d" % dis.opmap.get("ENTER_EXECUTOR", -1),
    ]
    lines += ["include=" + path for path in include_paths]
    lines += ["exclude=" + path for path in exclude_paths]
    return bytes(_STATE_HEADER_SIZE) + "\n".join(lines).encode("utf-8")


def follow_children(include_paths: t.Sequence[str], exclude_paths: t.Sequence[str]) -> None:
    """Make the Python child processes started from now on record their lines for this process.

    The bootstrap directory goes first on PYTHONPATH, so a Python child imports its
    sitecustomize. The child reads which context it runs for from the state file, or from the
    environment when it was started from another context than the one this process runs for,
    and writes its lines to a record file at exit. include_paths and exclude_paths select the
    files a child records, as they do here.
    """
    global _children, _original_execute_child

    if _children is not None or _linecov is None or not _enabled or _report_root is None or os.name == "nt":
        return
    # Imported here, not at module level: only a test session that follows children needs them.
    import shutil
    import subprocess  # nosec
    import tempfile

    directory = tempfile.mkdtemp(prefix="dd-coverage-")
    try:
        state = os.path.join(directory, "state")
        with open(state, "wb") as f:
            f.write(_children_config(include_paths, exclude_paths))
        _linecov.share_state(state)
    except Exception:
        log.debug("Child processes are not followed", exc_info=True)
        shutil.rmtree(directory, ignore_errors=True)
        return
    python_path = env.get("PYTHONPATH")
    _children = _ChildProcesses(directory, python_path)
    env["PYTHONPATH"] = _BOOTSTRAP_DIR + (os.pathsep + python_path if python_path else "")
    env[_CHILD_DIR_ENV] = directory
    if subprocess.Popen._execute_child is not _execute_child:  # type: ignore[attr-defined]
        _original_execute_child = subprocess.Popen._execute_child  # type: ignore[attr-defined]
        subprocess.Popen._execute_child = _execute_child  # type: ignore[attr-defined]
    _linecov.set_running(ctx_native.get())
    _linecov.set_records_callback(collect_children)
    forksafe.register(_stop_in_forked_child)


def _child_env(child_env: t.Any) -> t.Any:
    """The environment for a child that Popen starts.

    It names the context the child runs for when that is not the one the state file names, and
    keeps the bootstrap when the caller replaced the environment.
    """
    children = _children
    if children is None or _linecov is None:
        return child_env
    token = _running_token()
    if child_env is None:
        if token == _linecov.owner():
            return None
        # All of it, as the child would inherit it: env.copy() leaves out empty OTEL_* variables.
        child_env = dict(os.environ)  # ast-grep-ignore: os-environ-fix-access
    else:
        child_env = dict(child_env)
    as_bytes = any(isinstance(key, bytes) for key in child_env)

    def key(name: str) -> t.Any:
        return os.fsencode(name) if as_bytes else name

    def value(text: str) -> t.Any:
        return os.fsencode(text) if as_bytes else text

    python_path = child_env.get(key("PYTHONPATH"))
    entries = os.fsdecode(python_path).split(os.pathsep) if python_path else []
    if _BOOTSTRAP_DIR not in entries:
        child_env[key("PYTHONPATH")] = value(os.pathsep.join([_BOOTSTRAP_DIR] + entries))
    child_env[key(_CHILD_DIR_ENV)] = value(children.directory)
    child_env[key(_CHILD_OWNER_ENV)] = value(str(token))
    return child_env


def _running_token() -> int:
    running = ctx_native.get()
    return running.token if running is not None and running.active else 0


def process_record() -> t.Optional[tuple[str, int]]:
    """Where a multiprocessing child started now writes its lines, and for which context: the record
    directory and the token of the running context. None when child processes are not followed.
    """
    children = _children
    if _linecov is None:
        return None
    if children is None:
        # A multiprocessing child forked from the test process: its processes run for its context.
        return _forked_record
    return children.directory, _running_token()


# What process_record() returned for this process, when it is a multiprocessing child forked from
# the test process.
_forked_record: t.Optional[tuple[str, int]] = None


def run_forked_process(record: tuple[str, int], bootstrap: t.Any, process: t.Any, args: t.Any, kwargs: t.Any) -> t.Any:
    """Run BaseProcess._bootstrap in a multiprocessing child forked from this process.

    record is what process_record() returned when the process started. The lines the child runs
    from now on go to a record file for that context, as a child process's do; the lines recorded
    before the fork are this process's to report.
    """
    global _forked_record

    if _linecov is None or not _enabled:
        return bootstrap(process, *args, **kwargs)
    directory, owner = record
    _forked_record = record
    _linecov.forget()
    # Following stopped at the fork; the children of this child run for the same context.
    python_path = env.get("PYTHONPATH")
    entries = python_path.split(os.pathsep) if python_path else []
    if _BOOTSTRAP_DIR not in entries:
        env["PYTHONPATH"] = os.pathsep.join([_BOOTSTRAP_DIR] + entries)
    env[_CHILD_DIR_ENV] = directory
    return _child_coverage.run_process(_linecov, directory, owner, bootstrap, process, args, kwargs)


def _execute_child(
    self: t.Any,
    args: t.Any,
    executable: t.Any,
    preexec_fn: t.Any,
    close_fds: t.Any,
    pass_fds: t.Any,
    cwd: t.Any,
    env: t.Any,
    *rest: t.Any,
    **kwargs: t.Any,
) -> t.Any:
    if _children is not None:
        try:
            env = _child_env(env)
        except Exception:
            log.debug("Could not pass the coverage context to a child process", exc_info=True)
    return _original_execute_child(self, args, executable, preexec_fn, close_fds, pass_fds, cwd, env, *rest, **kwargs)


def collect_children() -> None:
    """Merge the records of the child processes that exited since the last call.

    A record joins the active context it names; once that context's window has closed, or when
    it names none, it joins the unowned lines. Cheap when no child wrote a record.
    """
    children = _children
    if children is None or _linecov is None:
        return
    records = _linecov.records()
    if records == children.records_seen:
        return
    children.records_seen = records
    try:
        entries = list(_posix.scandir(children.directory))
    except OSError:
        return
    for entry in entries:
        if not entry.name.endswith(".rec"):
            continue
        try:
            files = marshal.loads(_read_file(entry.path))  # nosec: written by this process's children
            _posix.unlink(entry.path)
            token = int(entry.name.split("-", 1)[0])
        except Exception:
            log.debug("Could not read the child coverage record %s", entry.path, exc_info=True)
            continue
        _merge_child_record(token, files)


def _read_file(path: str) -> bytes:
    fd = _posix.open(path, _posix.O_RDONLY)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = _posix.read(fd, 1 << 20)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        _posix.close(fd)


def _merge_child_record(token: int, files: dict[str, bytes]) -> None:
    assert _linecov is not None and _report_root is not None  # nosec
    for report_path, bitmap in files.items():
        path = _report_root + report_path.replace("/", os.sep)
        _linecov.merge(token, _file_id(path), bitmap)


def stop_following_children() -> None:
    """Restore the environment and remove the state directory. Records not collected are dropped."""
    children = _stop_following()
    if children is not None:
        import shutil

        forksafe.unregister(_stop_in_forked_child)
        shutil.rmtree(children.directory, ignore_errors=True)


def _stop_following() -> t.Optional[_ChildProcesses]:
    global _children

    children = _children
    if children is None:
        return None
    _children = None
    import subprocess  # nosec

    if subprocess.Popen._execute_child is _execute_child:  # type: ignore[attr-defined]
        subprocess.Popen._execute_child = _original_execute_child  # type: ignore[attr-defined]
    if children.python_path is None:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = children.python_path
    env.pop(_CHILD_DIR_ENV, None)
    if _linecov is not None:
        _linecov.set_records_callback(None)
        _linecov.unshare_state()
    return children


def _stop_in_forked_child() -> None:
    """A forked process stops following: it would publish its contexts in its parent's state file."""
    _stop_following()
