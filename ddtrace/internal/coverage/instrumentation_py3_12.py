"""
Coverage instrumentation for Python 3.12+ using the sys.monitoring API.

This module supports two modes:
1. Line-level coverage: Tracks which specific lines are executed (LINE events)
2. File-level coverage: Tracks which files are executed (PY_START events)

The mode is controlled by the _DD_COVERAGE_FILE_LEVEL environment variable.

Coverage does not claim its own sys.monitoring tool slot. It registers a
:class:`~ddtrace.internal.monitoring.MonitoringEventHandler` with the shared
ddtrace sys.monitoring multiplexer (``ddtrace.internal.monitoring``), which owns
a single tool slot on behalf of every ddtrace sub-system. This is what makes
coexistence with other tools safe:

* Internal sub-systems (the universal wrapping context, bytecode line-hook
  injection, and this coverage collector) all share the multiplexer's one slot
  and dispatch per code object, so they never collide with each other.
* External tools (e.g. coverage.py) use their own tool slots. The multiplexer's
  DISABLE optimisation is tool-scoped (a per-(tool, code, location) mark cleared
  by a tool-scoped ``set_local_events`` toggle in :func:`refresh`, never by the
  global ``sys.monitoring.restart_events()``), so it cannot corrupt another
  tool's disabled-event state regardless of timing.

Per-test re-arming is therefore also tool-scoped: :func:`_rearm_disabled`
refreshes only the code objects this collector silenced, instead of calling the
global ``restart_events()`` the previous direct-slot implementation relied on.
"""

import dis
import sys
from types import CodeType
import typing as t
import weakref

from bytecode import Bytecode

from ddtrace.internal import monitoring as _monitoring
from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.coverage.import_instrumentation_py3_12 import ImportName
from ddtrace.internal.coverage.import_instrumentation_py3_12 import ImportNamesByLine
from ddtrace.internal.coverage.import_instrumentation_py3_12 import import_names_by_line
from ddtrace.internal.coverage.import_instrumentation_py3_12 import inject_import_hooks
from ddtrace.internal.coverage.import_instrumentation_py3_12 import iter_import_events
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.threads import Lock
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.obfuscation import is_obfuscated_code


log = get_logger(__name__)

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

EXTENDED_ARG = dis.EXTENDED_ARG
RESUME = dis.opmap["RESUME"]
CACHE = 0  # CACHE opcode is always 0 across all CPython versions
LOAD_CONST = dis.opmap["LOAD_CONST"]
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
# LOAD_SMALL_INT was added in 3.14, replacing LOAD_CONST for small integer literals.
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
_ACCURATE_IMPORTS_REQUESTED = asbool(env.get("_DD_COVERAGE_ACCURATE_IMPORTS", "false"))
_USE_ACCURATE_IMPORTS = sys.version_info < (3, 15) and _ACCURATE_IMPORTS_REQUESTED
if _ACCURATE_IMPORTS_REQUESTED and not _USE_ACCURATE_IMPORTS:
    log.info(
        "_DD_COVERAGE_ACCURATE_IMPORTS is enabled, but accurate import tracking is not supported on Python %s; "
        "using conservative static import tracking instead",
        sys.version.split()[0],
    )

# TODO(py-315): Accurate import-hook injection (_DD_COVERAGE_ACCURATE_IMPORTS) is unsupported on
# 3.15+ because the `bytecode` library's CALL codegen segfaults on exec under CPython 3.15.0rc1,
# which is what ddtrace.internal.bytecode_injection.INJECTION_ASSEMBLY relies on to splice hook
# calls after import opcodes (see import_instrumentation_py3_12.inject_import_hooks). Re-enabling
# this needs either an upstream `bytecode` fix, or reimplementing injection on sys.monitoring
# INSTRUCTION events. Static import tracking (iter_import_events/import_names_by_line) already
# works on 3.15+ and is used as the fallback.

# The sys.monitoring event this collector listens on. The actual event enabled per code object
# is derived from the handler's overridden methods (PY_START for file-level, LINE for line-level)
# by the multiplexer; this constant is kept for observability and test compatibility.
_EVENT = sys.monitoring.events.PY_START if _USE_FILE_LEVEL_COVERAGE else sys.monitoring.events.LINE
# Backwards-compatible alias used by tests.
EVENT = _EVENT

# Store: (hook, path, import_names_by_line, line_hook, file_hook, import_hook)
# IMPORTANT: Do not change t.Dict/t.Tuple to dict/tuple until minimum Python version is 3.11+
# Module-level dict[...]/tuple[...] in Python 3.10 affects import timing. See packages.py for details.
LineHookType = t.Optional[t.Callable[[str, int], None]]  # noqa: UP006
FileHookType = t.Optional[t.Callable[[str], None]]  # noqa: UP006
ImportHookType = t.Optional[t.Callable[[str, ImportName], None]]  # noqa: UP006
CodeHookData = t.Tuple[HookType, str, ImportNamesByLine, LineHookType, FileHookType, ImportHookType]  # noqa: UP006
_CODE_HOOKS: t.Dict[CodeType, CodeHookData] = {}  # noqa: UP006

# Code objects whose LINE/PY_START events this collector has silenced by returning DISABLE. The
# multiplexer's DISABLE is tool-scoped, so to re-arm for the next test context we toggle
# set_local_events for each of these via monitoring.refresh() -- never the global
# restart_events(), which would reset every other tool's disabled-event state. Tracked as a
# WeakSet so code objects that are garbage-collected (e.g. eval'd code) disappear on their own.
_disabled_code: "weakref.WeakSet[CodeType]" = weakref.WeakSet()
_rearm_lock = Lock()

# Set to True once the multiplexer fails to acquire a tool slot, after which instrument_all_lines()
# becomes a no-op (graceful degradation, matching the previous direct-slot behaviour).
_multiplexer_unavailable: bool = False


class _CoverageFileHandler(_monitoring.MonitoringEventHandler):
    """Per-code-object handler dispatching file-level coverage via PY_START events."""

    def on_py_start(self, code: CodeType, instruction_offset: int) -> t.Optional[object]:
        hook_data = _CODE_HOOKS.get(code)
        if hook_data is None:
            return _monitoring._DISABLE
        hook, path, import_names, _line_hook, file_hook, import_hook = hook_data

        # Report file-level coverage using a dedicated hook. File-level coverage only means "this file executed";
        # import metadata is emitted separately below.
        if file_hook is not None:
            file_hook(path)
        else:
            hook((0, path, None))

        # Conservative static import metadata path. This is the default when accurate import-hook injection is off, and
        # also the fallback if injection fails. It is less precise because PY_START fires before guarded imports are
        # known to execute.
        for import_name in import_names.values():
            if import_hook is not None:
                import_hook(path, import_name)
            else:
                hook((0, path, import_name))

        with _rearm_lock:
            _disabled_code.add(code)
        return _monitoring._DISABLE


class _CoverageLineHandler(_monitoring.MonitoringEventHandler):
    """Per-code-object handler dispatching line-level coverage via LINE events."""

    def on_py_line(self, code: CodeType, line_number: int) -> t.Optional[object]:
        hook_data = _CODE_HOOKS.get(code)
        if hook_data is None:
            return _monitoring._DISABLE
        hook, path, import_names, line_hook, _file_hook, import_hook = hook_data

        if line_hook is not None:
            line_hook(path, line_number)
            if import_name := import_names.get(line_number, None):
                if import_hook is not None:
                    import_hook(path, import_name)
                else:
                    hook((line_number, path, import_name))
        else:
            import_name = import_names.get(line_number, None)
            hook((line_number, path, import_name))

        with _rearm_lock:
            _disabled_code.add(code)
        return _monitoring._DISABLE


# A single shared handler instance is registered for every instrumented code object; it dispatches
# by looking the code object up in _CODE_HOOKS. The multiplexer keys handlers per code object by
# identity, so one instance shared across many code objects is fine. Both handler classes are
# defined unconditionally so tests can exercise either mode regardless of the active env var.
_handler: _monitoring.MonitoringEventHandler = (
    _CoverageFileHandler() if _USE_FILE_LEVEL_COVERAGE else _CoverageLineHandler()
)


def _rearm_disabled() -> None:
    """Re-arm LINE/PY_START events silenced by this collector's DISABLE returns.

    Called from CollectInContext.__enter__ so each test context sees events fire again. Tool-scoped
    (monitoring.refresh() toggles set_local_events for our tool slot only), so it cannot affect any
    other sys.monitoring tool's disabled-event state, unlike the global restart_events() this used
    to call.
    """
    with _rearm_lock:
        if not _disabled_code:
            return
        codes = list(_disabled_code)
        _disabled_code.clear()
    for code in codes:
        _monitoring.refresh(code)


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

    Coverage registers a single handler with the shared sys.monitoring multiplexer instead of
    claiming its own tool slot. The handler returns sys.monitoring.DISABLE after recording so each
    line/file fires only once per test context (performance optimisation); _rearm_disabled()
    re-enables them between contexts via the tool-scoped monitoring.refresh().
    """
    global _multiplexer_unavailable

    if _multiplexer_unavailable:
        return code, CoverageLines()

    try:
        _monitoring.ensure_tool()
    except _monitoring.MonitoringToolUnavailable:
        _multiplexer_unavailable = True
        log.warning(
            "No sys.monitoring tool slot available for ddtrace, not gathering coverage. "
            "Disable a conflicting sys.monitoring tool to restore coverage."
        )
        return code, CoverageLines()

    return _instrument_with_monitoring(code, hook, path, package)


def _instrument_with_monitoring(
    code: CodeType, hook: HookType, path: str, package: str
) -> tuple[CodeType, CoverageLines]:
    """
    Instrument code using either LINE events for detailed line-by-line coverage or PY_START for file-level.
    """
    hook_self = getattr(hook, "__self__", None)
    line_hook = getattr(hook_self, "hook_line", None)
    file_hook = getattr(hook_self, "hook_file", None)
    import_hook = getattr(hook_self, "hook_import", None)
    collect_import_coverage = getattr(hook_self, "_collect_import_coverage", False)

    track_lines = not _USE_FILE_LEVEL_COVERAGE
    accurate_file_imports = _USE_FILE_LEVEL_COVERAGE and _USE_ACCURATE_IMPORTS and collect_import_coverage

    if accurate_file_imports:
        lines = CoverageLines()
        import_names: ImportNamesByLine = {}
    elif track_lines or collect_import_coverage:
        # Keep the default path cheap: use raw co_code scanning for line numbers and conservative import metadata.
        lines, import_names = _extract_lines_and_imports(
            code, package, track_lines=track_lines, collect_imports=collect_import_coverage
        )
    else:
        lines = CoverageLines()
        import_names = {}

    # Recursively instrument nested code objects first. sys.monitoring events must be enabled on the final code
    # objects, not on the original nested constants that may be replaced below.
    new_consts: t.Optional[list[t.Any]] = None
    for const_index, nested_code in enumerate(code.co_consts):
        if isinstance(nested_code, CodeType) and not is_obfuscated_code(nested_code):
            new_nested_code, nested_lines = instrument_all_lines(nested_code, hook, path, package)
            lines.update(nested_lines)
            if new_nested_code is not nested_code:
                if new_consts is None:
                    new_consts = list(code.co_consts)
                new_consts[const_index] = new_nested_code

    if new_consts is not None:
        code = code.replace(co_consts=tuple(new_consts))

    if _USE_FILE_LEVEL_COVERAGE:
        # In file-level mode, PY_START is too coarse for import dependency tracking: it fires when a code object
        # starts, before guarded imports are known to execute. Inject a tiny hook immediately after actual import
        # opcodes instead, and keep PY_START exclusively for file coverage.
        if accurate_file_imports:
            # Accurate mode needs Bytecode.from_code() for hook insertion points. Parse once after nested code objects
            # have been replaced, then use that same Bytecode object both to find import events and to inject hooks.
            bytecode = Bytecode.from_code(code)
            import_events = iter_import_events(bytecode, package, code)
            import_names = import_names_by_line(import_events)
            if code.co_name == "<module>" and package is not None:
                _add_package_dependency(import_names, 0, package)
            try:
                code = inject_import_hooks(bytecode, hook, path, import_events)
            except Exception:
                log.debug(
                    "Failed to inject import hooks into %r; falling back to static import metadata",
                    code,
                    exc_info=True,
                )
            else:
                # Keep the file-level package dependency sentinel. Import hooks cover actual import opcodes, but the
                # current module's dependency on its containing package is not backed by an import opcode.
                import_names = {0: import_names[0]} if 0 in import_names else {}

        # Register the multiplexer handler for this code object (enables local PY_START events).
        _monitoring.register(code, _handler)
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

    # Register the multiplexer handler for this code object (enables local LINE events).
    _monitoring.register(code, _handler)
    # Register the generic hook plus specialized hooks when the collector provides them. Keeping file-, line-, and
    # import-level operations separate makes the two coverage modes easier to follow and avoids tuple dispatch in the
    # common ModuleCodeCollector path.
    _CODE_HOOKS[code] = (hook, path, import_names, line_hook, file_hook, import_hook)

    return code, lines


def _add_package_dependency(
    import_names: ImportNamesByLine,
    package_dependency_line: int,
    package: str,
) -> None:
    """Record the current module's dependency on its containing package."""
    if package_dependency_line in import_names:
        existing_package, existing_names = import_names[package_dependency_line]
        import_names[package_dependency_line] = (existing_package or package, ("",) + existing_names)
    else:
        import_names[package_dependency_line] = (package, ("",))


def _extract_lines_and_imports(
    code: CodeType,
    package: str,
    track_lines: bool = True,
    collect_imports: bool = True,
) -> tuple[CoverageLines, ImportNamesByLine]:
    """Extract executable line numbers and conservative import metadata via raw bytecode iteration.

    This intentionally avoids Bytecode.from_code()/dis.get_instructions() in the default path. Accurate import hook
    injection needs richer bytecode objects, but conservative import metadata and line extraction can be decoded from
    CPython wordcode directly with much lower overhead.

    This raw scanner handles CPython 3.12+ bytecode details that are easy to lose when editing:
    CACHE entries must not enter the argument history; 3.14+ LOAD_SMALL_INT stores the integer directly instead of
    indexing co_consts; dis.findlinestarts() owns the version-specific line table decoding; and 3.15+ PEP 810
    bit-packs IMPORT_NAME's co_names index behind lazy-import flag bits.
    """
    lines = CoverageLines()
    import_names: ImportNamesByLine = {}

    current_arg: int = 0
    current_import_name: t.Optional[str] = None
    current_import_package: t.Optional[str] = None

    linestarts = dict(dis.findlinestarts(code))
    line: t.Optional[int] = None
    package_dependency_recorded = False

    # Track the decoded values of the previous two real instructions for import depth.
    # The import sequence is: LOAD_CONST/LOAD_SMALL_INT <level>, LOAD_CONST <fromlist>, IMPORT_NAME.
    # At IMPORT_NAME, prev_prev_value holds the decoded import depth.
    prev_prev_value: t.Any = 0
    prev_value: t.Any = 0

    ext: list[int] = []
    code_iter = iter(enumerate(code.co_code))
    try:
        while True:
            offset, opcode = next(code_iter)
            _, arg = next(code_iter)

            # Skip RESUME and CACHE entries (CACHE=0 on all CPython versions). CACHE entries must not pollute the
            # argument history used for import depth tracking.
            if opcode == RESUME or opcode == CACHE:
                continue

            if offset in linestarts:
                line = linestarts[offset]
                if line is not None:
                    if (
                        collect_imports
                        and code.co_name == "<module>"
                        and not package_dependency_recorded
                        and package is not None
                    ):
                        _add_package_dependency(import_names, 0 if _USE_FILE_LEVEL_COVERAGE else line, package)
                        package_dependency_recorded = True

                    if track_lines:
                        lines.add(line)

            if not collect_imports:
                continue

            if opcode == EXTENDED_ARG:
                ext.append(arg)
                continue

            current_arg = int.from_bytes([*ext, arg], "big", signed=False)
            ext.clear()

            if opcode == IMPORT_NAME and line is not None:
                import_depth = prev_prev_value if isinstance(prev_prev_value, int) else 0
                current_import_name = code.co_names[current_arg >> _IMPORT_NAME_ARG_SHIFT]
                current_import_package = (
                    ".".join(package.split(".")[: -import_depth + 1]) if import_depth > 1 else package
                )

                if line in import_names:
                    previous_package, previous_names = import_names[line]
                    import_names[line] = (
                        current_import_package or previous_package,
                        previous_names + (current_import_name,),
                    )
                else:
                    import_names[line] = (current_import_package, (current_import_name,))

            # Also track import-from statements since the imported attribute can itself be a module, eg:
            # from my_package import my_module
            if opcode == IMPORT_FROM and line is not None and current_import_name is not None:
                import_from_name = f"{current_import_name}.{code.co_names[current_arg]}"
                if line in import_names:
                    previous_package, previous_names = import_names[line]
                    import_names[line] = (
                        current_import_package or previous_package,
                        previous_names + (import_from_name,),
                    )
                else:
                    import_names[line] = (current_import_package or package, (import_from_name,))

            # Decode argument value and shift history after opcode handling. IMPORT_NAME reads
            # prev_prev_value before this block because the import sequence is level, fromlist, IMPORT_NAME.
            if opcode == LOAD_CONST:
                decoded = code.co_consts[current_arg]
            elif LOAD_SMALL_INT is not None and opcode == LOAD_SMALL_INT:
                decoded = current_arg
            else:
                decoded = current_arg
            prev_prev_value = prev_value
            prev_value = decoded

    except StopIteration:
        pass

    return lines, import_names
