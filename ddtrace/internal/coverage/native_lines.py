"""Native per-test line coverage on sys.monitoring LINE events (CPython 3.12+).

The LINE callback is the C function _linecov.line_event. It credits each line to the context held
in ctx_native when that context is active (a test, or a suite-scope fixture setup), to the file's
initialiser lines when the code is a module or class body, and to the process's unowned lines
otherwise. See _linecov.c for the attribution and re-arm rules.

Enabled with _DD_COVERAGE_NATIVE_LINES=true, which also selects line-level coverage.
"""

from contextvars import ContextVar
import dis
import marshal
import os
import sys
from types import CodeType
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
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

_requested: bool = sys.version_info >= (3, 12) and asbool(env.get("_DD_COVERAGE_NATIVE_LINES", "false"))
_enabled: bool = False
_report_root: t.Optional[str] = None
_file_ids: dict[str, int] = {}


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


def init(tool_id: int) -> bool:
    """Register the native callbacks for tool_id. Returns False when native collection is unusable."""
    global _enabled

    if not requested() or _linecov is None:
        return False
    try:
        events = _monitoring.events
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
        )
        _monitoring.register_callback(tool_id, events.LINE, _linecov.line_event)
        if _linecov.HAVE_INSTRUCTION_PROBES:
            _monitoring.register_callback(tool_id, events.INSTRUCTION, _linecov.instruction_event)
    except Exception:
        log.debug("Native line coverage unavailable", exc_info=True)
        return False

    _enabled = True
    if internal and not _self_test(tool_id):
        _linecov.set_internal_rearm(False)
        log.debug("Internal re-arm self-test failed; using the public re-arm")
    elif _linecov.stats()["instruction_probes"] and not _self_test(tool_id, distant_lines=True):
        _linecov.set_instruction_probes(False)
        log.debug("INSTRUCTION probe self-test failed; using LINE events only")
    return True


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
    forksafe.register(_stop_in_forked_child)


def _child_env(child_env: t.Any) -> t.Any:
    """The environment for a child that Popen starts.

    It names the context the child runs for when that is not the one the state file names, and
    keeps the bootstrap when the caller replaced the environment.
    """
    children = _children
    if children is None or _linecov is None:
        return child_env
    running = ctx_native.get()
    token = running.token if running is not None and running.active else 0
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
        entries = list(os.scandir(children.directory))
    except OSError:
        return
    for entry in entries:
        if not entry.name.endswith(".rec"):
            continue
        try:
            with open(entry.path, "rb") as f:
                files = marshal.load(f)  # nosec: written by this process's children
            os.unlink(entry.path)
            token = int(entry.name.split("-", 1)[0])
        except Exception:
            log.debug("Could not read the child coverage record %s", entry.path, exc_info=True)
            continue
        _merge_child_record(token, files)


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
        _linecov.unshare_state()
    return children


def _stop_in_forked_child() -> None:
    """A forked process stops following: it would publish its contexts in its parent's state file."""
    _stop_following()
