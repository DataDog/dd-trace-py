"""Native per-test line coverage on sys.monitoring LINE events (CPython 3.12+).

The LINE callback is the C function _linecov.line_event. It credits each line to the context held
in ctx_native when that context is active (a test, or a suite-scope fixture setup), to the file's
initialiser lines when the code is a module or class body, and to the process's unowned lines
otherwise. See _linecov.c for the attribution and re-arm rules.

Enabled with _DD_COVERAGE_NATIVE_LINES=true, which also selects line-level coverage.
"""

from contextvars import ContextVar
import dis
import os
import sys
from types import CodeType
import typing as t

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
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return None
    if relative == os.curdir or relative.startswith(os.pardir):
        return None
    return "/" + relative.replace(os.sep, "/")


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


def shared() -> dict[str, list[tuple[str, bytes]]]:
    """The process's Shared record: initialiser, suite-setup and unowned lines, per file."""
    if _linecov is None or not _enabled:
        return {}
    return t.cast(dict[str, list[tuple[str, bytes]]], _linecov.shared())


def stats() -> dict[str, t.Any]:
    return _linecov.stats() if _linecov is not None else {}
