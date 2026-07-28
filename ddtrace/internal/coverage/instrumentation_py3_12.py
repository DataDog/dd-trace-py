"""
Coverage instrumentation for Python 3.12+ using sys.monitoring API.

This module supports two modes:
1. Line-level coverage: Tracks which specific lines are executed (LINE events)
2. File-level coverage: Tracks which files are executed (PY_START events)

The mode is controlled by the _DD_COVERAGE_FILE_LEVEL environment variable.
"""

import dis
import sys
from types import CodeType
import typing as t

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
RESUME = dis.opmap["RESUME"]
CACHE = 0  # CACHE opcode is always 0 across all CPython versions
LOAD_CONST = dis.opmap["LOAD_CONST"]
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
# LOAD_SMALL_INT was added in 3.14, replacing LOAD_CONST for small integer literals
LOAD_SMALL_INT = dis.opmap.get("LOAD_SMALL_INT")

# In Python 3.15 (PEP 810 lazy imports), IMPORT_NAME's arg is bit-packed:
# bits 2+ = name index into co_names, bits 0-1 = lazy/eager flags.
# So the index is arg >> 2. On 3.12-3.14, arg is a plain index (shift by 0).
_IMPORT_NAME_ARG_SHIFT = 2 if sys.version_info >= (3, 15) else 0

# Detect empty modules: the bytecode pattern varies across Python versions.
# Python 3.12-3.13: RESUME + RETURN_CONST
# Python 3.14: RESUME + LOAD_CONST + RETURN_VALUE (RETURN_CONST was removed)
# Python 3.15+: same as 3.14 but RESUME has a CACHE entry (extra 2 bytes)
# Instead of hardcoding, just compile an empty module to get the expected bytes.
EMPTY_MODULE_BYTES = compile("", "<empty>", "exec").co_code

# Check if file-level coverage is requested
_USE_FILE_LEVEL_COVERAGE = asbool(env.get("_DD_COVERAGE_FILE_LEVEL", "true"))

EVENT = sys.monitoring.events.PY_START if _USE_FILE_LEVEL_COVERAGE else sys.monitoring.events.LINE

# NOTE: We try tool slots in priority order (4, 3, 1) to avoid colliding with other tools.
# Slot 4 is preferred; slot 1 (COVERAGE_ID) is a last resort since other coverage tools may use it.
# _DD_TOOL_ID is None until register_coverage() succeeds; instrument_all_lines() is a no-op
# while it remains None.
_DD_TOOL_ID: t.Optional[int] = None  # noqa: UP006
_DD_CANDIDATE_SLOTS = (4, 3, 1)

# Store: (hook, path, import_names_by_line, line_hook, file_hook, import_hook)
# IMPORTANT: Do not change t.Dict/t.Tuple to dict/tuple until minimum Python version is 3.11+
# Module-level dict[...]/tuple[...] in Python 3.10 affects import timing. See packages.py for details.
ImportName = t.Tuple[str, t.Optional[t.Tuple[str]]]  # noqa: UP006
ImportNamesByLine = t.Dict[int, ImportName]  # noqa: UP006
LineHookType = t.Optional[t.Callable[[str, int], None]]  # noqa: UP006
FileHookType = t.Optional[t.Callable[[str], None]]  # noqa: UP006
ImportHookType = t.Optional[t.Callable[[str, ImportName], None]]  # noqa: UP006
CodeHookData = t.Tuple[HookType, str, ImportNamesByLine, LineHookType, FileHookType, ImportHookType]  # noqa: UP006
_CODE_HOOKS: t.Dict[CodeType, CodeHookData] = {}  # noqa: UP006

# NOTE: When True (default), _event_handler returns sys.monitoring.DISABLE after recording a
# line so Python stops firing that event for this code location — a performance optimisation that
# avoids redundant callbacks in loops.  CollectInContext.__enter__ then calls restart_events() at
# the start of each test to re-enable them (safe there: it only fires while we believe no other
# tool is registered, so there is nothing else to corrupt).
# Automatically set to False when another sys.monitoring tool (e.g. coverage.py) is detected via
# has_other_monitoring_tools(): without this, our own DISABLE'd lines could only be re-armed via
# the global restart_events(), which would also reset that other tool's disabled-event state,
# corrupting its data.  Without DISABLE, events keep firing on every execution (slightly slower but
# still correct, since CoverageLines.add() is idempotent).
# The flag is re-evaluated in CollectInContext.__enter__ via update_disable_optimization().
# On the True→False transition, _rearm_all_events() re-enables events that were DISABLE'd during
# the window between our install and the other tool registering (e.g. early conftest imports in
# pytest happen before coverage.py registers in pytest_configure).  It does this via a per-code-
# object set_local_events() toggle rather than the global restart_events() — verified empirically
# (see _rearm_all_events()'s docstring) to only affect our own tool's state, leaving any other
# registered tool's disabled-event state untouched.
_use_disable_optimization: bool = True


def has_other_monitoring_tools() -> bool:
    """Check whether any non-datadog tool is registered with sys.monitoring.

    Iterates all six tool slots (0-5) and returns True if any slot other than ours is occupied.
    This is used to decide whether the DISABLE optimisation (and the global restart_events() call
    it requires) is safe to use.

    Note: this can't see the two legacy tool slots that back sys.settrace()/sys.setprofile()-based
    debuggers and profilers. restart_events()'s global reach does include those, but resetting a
    legacy tracer's own DISABLE-equivalent state isn't a correctness issue for it, so this blind
    spot is treated as acceptable.
    """
    for tool_id in range(6):
        if tool_id == _DD_TOOL_ID:
            continue
        if sys.monitoring.get_tool(tool_id):
            return True
    return False


def _rearm_all_events() -> None:
    """Re-enable Datadog line events that were DISABLE'd during the early-import window.

    In CPython 3.12+, returning sys.monitoring.DISABLE from a callback replaces specific
    bytecode instructions with their non-instrumented variants. Two APIs can undo that:

    - Calling set_local_events(tool_id, code, event_set) with the SAME event_set it already has
      is a guaranteed no-op: CPython's implementation short-circuits (returns immediately,
      skipping re-instrumentation) whenever the event set passed is identical to what's already
      recorded for that tool+code object. This is deterministic, not a version inconsistency —
      it's just why this function can't simply call set_local_events(_DD_TOOL_ID, code, EVENT).
    - Toggling instead — set_local_events(_DD_TOOL_ID, code, 0) immediately followed by
      set_local_events(_DD_TOOL_ID, code, EVENT) — passes a genuinely different value on the
      first call, which does pass through CPython's real re-instrumentation path and clears our
      own DISABLE marks for that code object. Verified empirically (LINE events, PY_START events,
      and nested code objects) that this toggle only affects the calling tool's own state —
      another tool's DISABLE'd locations are provably left untouched, unlike the global
      sys.monitoring.restart_events() this function used to call.

    We loop over _CODE_HOOKS — the registry of every code object we've instrumented, populated in
    _instrument_with_monitoring() and never cleared — reusing it here for a second purpose:
    finding every code object that might still have a stale DISABLE mark to clear.

    Called on the True→False transition in update_disable_optimization(), i.e. when another
    sys.monitoring tool (e.g. coverage.py) is detected AFTER some events were already DISABLE'd
    during the window between our install and the other tool registering (e.g. early conftest
    imports in pytest happen before coverage.py registers in pytest_configure). Being tool-scoped,
    this no longer depends on careful timing to be safe — it cannot affect any other tool's
    disabled-event state regardless of when it runs.
    """
    for code in _CODE_HOOKS:
        sys.monitoring.set_local_events(_DD_TOOL_ID, code, 0)
        sys.monitoring.set_local_events(_DD_TOOL_ID, code, EVENT)


def update_disable_optimization() -> bool:
    """Re-evaluate _use_disable_optimization based on the current sys.monitoring state.

    Called from CollectInContext.__enter__ so that the flag is always in sync with the actual
    set of registered monitoring tools (e.g. coverage.py may have started after our install).

    When another tool is detected for the first time (True→False transition), any events that
    were DISABLE'd during the early-import window (before the other tool registered) are
    re-armed via _rearm_all_events() so per-test contexts don't miss those lines.

    Returns the new value of _use_disable_optimization.
    """
    global _use_disable_optimization
    prev = _use_disable_optimization
    _use_disable_optimization = not has_other_monitoring_tools()
    if prev and not _use_disable_optimization:
        # We were using DISABLE but another tool just appeared.  Re-arm events that were
        # disabled during the window before the other tool registered via _rearm_all_events()
        # (see its docstring for the tool-scoped set_local_events() toggle it uses).
        _rearm_all_events()
    return _use_disable_optimization


def _ensure_registered() -> bool:
    """Claim a tool slot on first call; return True if registered, False if all slots are taken."""
    global _DD_TOOL_ID

    if _DD_TOOL_ID is not None and sys.monitoring.get_tool(_DD_TOOL_ID) == "datadog":
        return True

    for slot in _DD_CANDIDATE_SLOTS:
        try:
            sys.monitoring.use_tool_id(slot, "datadog")
            _DD_TOOL_ID = slot
            break
        except ValueError:
            continue
    else:
        log.warning(
            "No sys.monitoring tool slot available (tried slots %s), not gathering coverage",
            _DD_CANDIDATE_SLOTS,
        )
        return False

    mode = "file-level" if _USE_FILE_LEVEL_COVERAGE else "line-level"
    log.debug("Registered %s coverage tool (tool_id=%d)", mode, _DD_TOOL_ID)
    sys.monitoring.register_callback(_DD_TOOL_ID, EVENT, _event_handler)
    return True


def instrument_all_lines(code: CodeType, hook: HookType, path: str, package: str) -> tuple[CodeType, CoverageLines]:
    """
    Instrument code for coverage tracking using Python 3.12's monitoring API.

    This function supports two modes based on _DD_COVERAGE_FILE_LEVEL:
    - Line-level: Uses LINE events for detailed line-by-line coverage
    - File-level (default): Uses PY_START events for faster file-level coverage

    Args:
        code: The code object to instrument
        hook: The hook function to call
        path: The file path
        package: The package name

    Returns:
        Tuple of (code object, CoverageLines with instrumentable lines)

    Note: By default callbacks return DISABLE after recording so each line fires only once per
    test context (performance optimisation).  When _use_disable_optimization is False the callback
    returns None instead, trading some performance for compatibility with other sys.monitoring tools.
    """
    if not _ensure_registered():
        return code, CoverageLines()

    return _instrument_with_monitoring(code, hook, path, package)


def _event_handler(code: CodeType, line: int) -> t.Optional[t.Literal[sys.monitoring.DISABLE]]:
    """
    Callback for LINE/PY_START events.

    When _use_disable_optimization is True (default), returns sys.monitoring.DISABLE after
    recording so Python stops firing events for this code location — a performance win for
    loops and hot paths.  CollectInContext then calls restart_events() between tests to
    re-enable them.

    When _use_disable_optimization is False (set when another sys.monitoring tool such as
    coverage.py is active), returns None so events keep firing.  This is slightly slower but
    means restart_events() is never needed, leaving the other tool's state untouched.
    """
    hook_data = _CODE_HOOKS.get(code)
    if hook_data is None:
        return sys.monitoring.DISABLE

    hook, path, import_names, line_hook, file_hook, import_hook = hook_data

    if _USE_FILE_LEVEL_COVERAGE:
        # Report file-level coverage using a dedicated hook. File-level coverage only means "this file executed";
        # import metadata is emitted separately below.
        if file_hook is not None:
            file_hook(path)
        else:
            hook((0, path, None))

        # Fallback path for import dependencies when bytecode import-hook injection is unavailable. This is less
        # precise because PY_START fires before guarded imports are known to execute.
        for import_name in import_names.values():
            if import_hook is not None:
                import_hook(path, import_name)
            else:
                hook((0, path, import_name))
    else:
        if line_hook is not None:
            line_hook(path, line)
            if import_name := import_names.get(line, None):
                if import_hook is not None:
                    import_hook(path, import_name)
                else:
                    hook((line, path, import_name))
        else:
            import_name = import_names.get(line, None)
            hook((line, path, import_name))

    if _use_disable_optimization:
        return sys.monitoring.DISABLE
    return None


def _instrument_with_monitoring(
    code: CodeType, hook: HookType, path: str, package: str
) -> tuple[CodeType, CoverageLines]:
    """
    Instrument code using either LINE events for detailed line-by-line coverage or PY_START for file-level.
    """
    track_lines = not _USE_FILE_LEVEL_COVERAGE
    # Extract import names and collect line numbers before rewriting nested code objects.
    lines, import_names = _extract_lines_and_imports(code, package, track_lines=track_lines)

    # Recursively instrument nested code objects first. sys.monitoring events must be enabled on the final code
    # objects, not on the original nested constants that may be replaced below.
    new_consts: t.Optional[list[t.Any]] = None
    for const_index, nested_code in enumerate(code.co_consts):
        if isinstance(nested_code, CodeType):
            new_nested_code, nested_lines = instrument_all_lines(nested_code, hook, path, package)
            lines.update(nested_lines)
            if new_nested_code is not nested_code:
                if new_consts is None:
                    new_consts = list(code.co_consts)
                new_consts[const_index] = new_nested_code

    if new_consts is not None:
        code = code.replace(co_consts=tuple(new_consts))

    hook_self = getattr(hook, "__self__", None)
    line_hook = getattr(hook_self, "hook_line", None)
    file_hook = getattr(hook_self, "hook_file", None)
    import_hook = getattr(hook_self, "hook_import", None)

    if _USE_FILE_LEVEL_COVERAGE:
        # In file-level mode, PY_START is too coarse for import dependency tracking: it fires when a code object
        # starts, before guarded imports are known to execute. Inject a tiny hook immediately after actual import
        # opcodes instead, and keep PY_START exclusively for file coverage.
        if import_names and getattr(hook_self, "_collect_import_coverage", False):
            try:
                code = _inject_import_hooks(code, hook, path, package)
            except Exception:
                log.debug(
                    "Failed to inject import hooks into %r; falling back to static import metadata", code, exc_info=True
                )
            else:
                import_names = {}

        # Enable local PY_START events for the final code object.
        sys.monitoring.set_local_events(_DD_TOOL_ID, code, EVENT)  # noqa
        _CODE_HOOKS[code] = (hook, path, import_names, line_hook, file_hook, import_hook)

        # Return CoverageLines with line 0 as sentinel to indicate file-level coverage.
        lines = CoverageLines()
        lines.add(0)
        return code, lines

    # Special case for empty modules (eg: __init__.py ):
    # Make sure line 0 is marked as executable, and add package dependency
    if not lines and code.co_name == "<module>" and code.co_code == EMPTY_MODULE_BYTES:
        lines.add(0)
        if package is not None:
            import_names[0] = (package, ("",))

    # Enable local LINE events for the final code object.
    sys.monitoring.set_local_events(_DD_TOOL_ID, code, EVENT)  # noqa
    # Register the generic hook plus specialized hooks when the collector provides them. Keeping file-, line-, and
    # import-level operations separate makes the two coverage modes easier to follow and avoids tuple dispatch in the
    # common ModuleCodeCollector path.
    _CODE_HOOKS[code] = (hook, path, import_names, line_hook, file_hook, import_hook)

    return code, lines


def _import_call_instructions(hook: HookType, arg: tuple[int, str, tuple[str, tuple[str, ...]]], lineno: int):
    """Build bytecode instructions for hook(arg) on Python 3.12+.

    AIDEV-NOTE: This helper intentionally mirrors ddtrace.internal.bytecode_injection's call sequence for
    Python 3.12/3.13+. If CPython call bytecode changes again, update both places together.
    """
    if sys.version_info >= (3, 13):
        return [
            Instr("LOAD_CONST", hook, lineno=lineno),
            Instr("PUSH_NULL", lineno=lineno),
            Instr("LOAD_CONST", arg, lineno=lineno),
            Instr("CALL", 1, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
        ]
    return [
        Instr("PUSH_NULL", lineno=lineno),
        Instr("LOAD_CONST", hook, lineno=lineno),
        Instr("LOAD_CONST", arg, lineno=lineno),
        Instr("CALL", 1, lineno=lineno),
        Instr("POP_TOP", lineno=lineno),
    ]


def _resolve_import_package(package: str, import_depth: int) -> str:
    return ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package


def _inject_import_hooks(code: CodeType, hook: HookType, path: str, package: str) -> CodeType:
    """Inject import dependency hooks immediately after actual import bytecodes.

    File-level coverage uses PY_START, which is too early to know whether guarded imports in the code object will run.
    These injected hooks fire only when the interpreter reaches IMPORT_NAME/IMPORT_FROM, so false runtime branches do
    not create dependency edges. The hook is inserted after the import opcode, meaning failed imports are not recorded.
    """
    bytecode = Bytecode.from_code(code)

    pending_insertions: list[tuple[int, tuple[int, str, tuple[str, tuple[str, ...]]], int]] = []
    current_import_name: t.Optional[str] = None
    current_import_package: str = package
    previous_previous_arg: t.Any = 0
    previous_arg: t.Any = 0

    for idx, instr in enumerate(bytecode):
        if not isinstance(instr, Instr):
            continue

        if instr.name == "IMPORT_NAME":
            import_depth = previous_previous_arg if isinstance(previous_previous_arg, int) else 0
            current_import_name = t.cast(str, instr.arg)
            current_import_package = _resolve_import_package(package, import_depth)
            pending_insertions.append(
                (idx, (0, path, (current_import_package, (current_import_name,))), instr.lineno or code.co_firstlineno)
            )
        elif instr.name == "IMPORT_FROM" and current_import_name is not None:
            import_from_name = f"{current_import_name}.{instr.arg}"
            pending_insertions.append(
                (idx, (0, path, (current_import_package, (import_from_name,))), instr.lineno or code.co_firstlineno)
            )

        if instr.name == "LOAD_CONST":
            decoded = instr.arg
        else:
            decoded = instr.arg if isinstance(instr.arg, int) else 0
        previous_previous_arg = previous_arg
        previous_arg = decoded

    for idx, arg, lineno in reversed(pending_insertions):
        bytecode[idx + 1 : idx + 1] = _import_call_instructions(hook, arg, lineno)

    return bytecode.to_code()


def _extract_lines_and_imports(
    code: CodeType, package: str, track_lines: bool = True
) -> tuple[CoverageLines, dict[int, tuple[str, tuple[str, ...]]]]:
    """Extract line numbers and import information via raw bytecode iteration.

    Iterates the 2-byte wordcode directly to avoid the overhead of creating
    Instruction namedtuples via dis.get_instructions().

    Handles all Python 3.12+ version differences:
    - Skips CACHE entries (opcode 0) so they don't corrupt argument history
    - Tracks decoded values (not raw args) for import depth, which handles both
      LOAD_CONST (indexes co_consts) and LOAD_SMALL_INT (3.14+, arg IS value)
    - Uses dis.findlinestarts() for version-agnostic line number mapping
    - On 3.15+ (PEP 810 lazy imports), IMPORT_NAME's arg is bit-packed:
      bits 2+ = name index into co_names, bits 0-1 = lazy/eager flags.
      _IMPORT_NAME_ARG_SHIFT (=2 on 3.15+, =0 otherwise) extracts the name index.
    - IMPORT_FROM is unaffected by PEP 810 — still uses plain co_names index.
    """
    lines = CoverageLines()
    import_names: dict[int, tuple[str, tuple[str, ...]]] = {}

    current_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    # Track line numbers
    linestarts = dict(dis.findlinestarts(code))
    line: t.Optional[int] = None

    # Track the decoded values of the previous two real instructions for import depth.
    # The import sequence is: LOAD_CONST/LOAD_SMALL_INT <level>, LOAD_CONST <fromlist>, IMPORT_NAME
    # At IMPORT_NAME, _prev_prev_val holds the decoded import depth.
    _prev_prev_val: t.Any = 0
    _prev_val: t.Any = 0

    ext: list[bytes] = []
    code_iter = iter(enumerate(code.co_code))
    try:
        while True:
            offset, opcode = next(code_iter)
            _, arg = next(code_iter)

            # Skip RESUME and CACHE entries (CACHE=0 on all CPython versions).
            # CACHE entries appear after some opcodes (e.g. RESUME on 3.15+) and
            # must not pollute the argument history used for import depth tracking.
            if opcode == RESUME or opcode == CACHE:
                continue

            if offset in linestarts:
                line = linestarts[offset]
                # Skip if line is None (bytecode that doesn't map to a specific source line)
                if line is not None:
                    # Make sure that the current module is marked as depending on its own package by instrumenting the
                    # first executable line. In file-level mode, line 0 is the sentinel for the executed module.
                    if code.co_name == "<module>" and not import_names and package is not None:
                        import_names[0 if _USE_FILE_LEVEL_COVERAGE else line] = (package, ("",))

                    if track_lines:
                        lines.add(line)

            if opcode is EXTENDED_ARG:
                ext.append(arg)
                continue
            else:
                current_arg = int.from_bytes([*ext, arg], "big", signed=False)
                ext.clear()

            if opcode == IMPORT_NAME and line is not None:
                # _prev_prev_val is the decoded import depth (from LOAD_CONST or LOAD_SMALL_INT)
                import_depth: int = _prev_prev_val
                current_import_name = code.co_names[current_arg >> _IMPORT_NAME_ARG_SHIFT]
                # Adjust package name if the import is relative and a parent (ie: if depth is more than 1)
                current_import_package = (
                    ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package
                )

                if line in import_names:
                    import_names[line] = (
                        current_import_package,
                        tuple(list(import_names[line][1]) + [current_import_name]),
                    )
                else:
                    import_names[line] = (current_import_package, (current_import_name,))

            # Also track import from statements since it's possible that the "from" target is a module, eg:
            # from my_package import my_module
            # Since the package has not changed, we simply extend the previous import names with the new value
            if opcode == IMPORT_FROM and line is not None:
                import_from_name = f"{current_import_name}.{code.co_names[current_arg]}"
                if line in import_names:
                    import_names[line] = (
                        current_import_package,
                        tuple(list(import_names[line][1]) + [import_from_name]),
                    )
                else:
                    import_names[line] = (current_import_package, (import_from_name,))

            # Decode argument value and shift history AFTER opcode handling.
            # LOAD_CONST indexes co_consts; LOAD_SMALL_INT (3.14+) uses the arg directly.
            # This must happen after IMPORT_NAME reads _prev_prev_val, not before.
            if opcode == LOAD_CONST:
                decoded = code.co_consts[current_arg]
            elif LOAD_SMALL_INT is not None and opcode == LOAD_SMALL_INT:
                decoded = current_arg
            else:
                decoded = current_arg
            _prev_prev_val = _prev_val
            _prev_val = decoded

    except StopIteration:
        pass

    return lines, import_names
