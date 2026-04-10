"""Bytecode instrumentation for SCA detection.

Provides functionality to instrument Python functions at runtime using
bytecode injection via dd-trace-py's bytecode_injection infrastructure.
"""

from __future__ import annotations

from threading import Lock
import types
from types import FunctionType
from typing import TYPE_CHECKING
from typing import Optional

from ddtrace.appsec._patch_utils import get_caller_frame_info
from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from ddtrace.appsec.sca._registry import InstrumentationRegistry
    from ddtrace.internal.telemetry.writer import TelemetryWriter


log = get_logger(__name__)


def _first_instr_line(code: types.CodeType) -> int:
    """Return the line number of the first real bytecode instruction.

    On Python <3.11, co_firstlineno is the ``def`` line, which has no
    bytecode instructions.  inject_hook needs the first *instruction*
    line, which is the first line of the function body.

    Handles the cross-version difference:
    - Python <3.12: starts_line is an int (the absolute line number) or None
    - Python >=3.12: starts_line is a bool; line_number has the actual int
    """
    import dis

    for instr in dis.get_instructions(code):
        # Python >=3.12: starts_line is bool, line_number is the int
        line = getattr(instr, "line_number", None)
        if line is not None:
            return line
        # Python <3.12: starts_line is the int line number
        if instr.starts_line is not None and isinstance(instr.starts_line, int):
            return instr.starts_line
    return code.co_firstlineno


def _get_caller_info() -> tuple[str, int, str]:
    """Walk the stack to find the first user-code caller frame.

    Returns (path, line, symbol) where:
    - path: relative file path of the caller
    - line: line number in the caller
    - symbol: function (or Class.method) name of the caller

    AIDEV-NOTE: Delegates to the shared get_caller_frame_info() which uses
    IAST's native C get_info_frame() + rel_path().  SCA just reshapes the
    4-tuple into a 3-tuple by combining class_name and function_name.
    """
    try:
        file_name, line_number, function_name, class_name = get_caller_frame_info()
        if not file_name:
            return "", 0, ""

        symbol = f"{class_name}.{function_name}" if class_name else (function_name or "")
        return file_name, line_number or 0, symbol
    except Exception:
        log.debug("Failed to get caller info via get_caller_frame_info", exc_info=True)
        return "", 0, ""


# AIDEV-NOTE: _registry is read on every hook invocation (hot path).
# We do NOT acquire a lock for reads — Python's GIL makes reference reads
# safe, and the reference is only set once at startup via set_registry().
_registry: Optional[InstrumentationRegistry] = None
# _telemetry_writer is lazily cached on first hook invocation to avoid
# the overhead of import-lock acquisition on every call.
_telemetry_writer = None

# Singleton Instrumenter instance, shared across all instrumentation
# operations so per-target locks are preserved.
_instrumenter_instance: Optional[Instrumenter] = None
_instrumenter_lock = Lock()


def _reset_after_fork() -> None:
    """Reset module-level references after fork."""
    global _registry, _telemetry_writer, _instrumenter_instance, _instrumenter_lock
    _registry = None
    _telemetry_writer = None
    _instrumenter_instance = None
    _instrumenter_lock = Lock()


# AIDEV-NOTE: Do NOT use os.register_at_fork here.  ddtrace's forksafe
# mechanism calls product.restart() which explicitly calls _reset_after_fork()
# before re-initializing.  If we also register with os.register_at_fork, the
# CPython callback fires AFTER restart() has already set up the new state,
# wiping _registry=None permanently.  See system_tests_error.md for details.


def set_registry(registry: InstrumentationRegistry) -> None:
    """Set global registry reference. Called once at startup."""
    global _registry
    _registry = registry


def _get_telemetry_writer() -> TelemetryWriter:
    """Lazily cache and return the telemetry writer singleton."""
    global _telemetry_writer
    if _telemetry_writer is None:
        from ddtrace.internal.telemetry import telemetry_writer

        _telemetry_writer = telemetry_writer
    return _telemetry_writer


def get_instrumenter(registry: InstrumentationRegistry) -> Instrumenter:
    """Get or create the singleton Instrumenter instance."""
    global _instrumenter_instance
    with _instrumenter_lock:
        if _instrumenter_instance is None:
            _instrumenter_instance = Instrumenter(registry)
        return _instrumenter_instance


def sca_detection_hook(qualified_name: str) -> None:
    """Hook injected into instrumented functions.

    Called at entry point of every instrumented function.
    Looks up vulnerability info from the registry and reports it
    to telemetry via attach_dependency_metadata.

    CRITICAL: MUST NOT throw exceptions — runs in customer code.
    """
    try:
        # AIDEV-NOTE: no lock needed — _registry is a simple reference read
        # protected by the GIL, and only set once at startup.
        registry_ref = _registry
        if not registry_ref:
            return

        registry_ref.record_hit(qualified_name)

        target_info = registry_ref.get_target_info(qualified_name)
        if not target_info or not target_info.cve_ids or not target_info.package_name:
            return

        writer = _get_telemetry_writer()
        # AIDEV-NOTE: Walk the stack to find the user-code frame that called
        # the vulnerable function, similar to IAST's _compute_file_line.
        # Reports the caller's path/line/symbol, not the target function's.
        caller_path, caller_line, caller_symbol = _get_caller_info()

        # AIDEV-NOTE: If the native frame walker can't find user code (e.g.,
        # deep wrapt/gevent stack), fall back to the target's own qualified
        # name so the backend knows the function was reached.  Without this,
        # add_metadata's `if path` guard silently drops the finding and
        # reached stays [].
        if not caller_path:
            # Use "module.path:Class.method" as path, the symbol part after ":"
            parts = qualified_name.split(":", 1)
            caller_path = parts[0] if parts else qualified_name
            caller_symbol = parts[1] if len(parts) > 1 else ""
            caller_line = 0

        for cve_id in target_info.cve_ids:
            writer.attach_dependency_metadata(target_info.package_name, cve_id, caller_path, caller_symbol, caller_line)

    except Exception:
        log.debug("SCA detection hook error for %s", qualified_name, exc_info=True)


class Instrumenter:
    """Manages bytecode instrumentation of target functions."""

    def __init__(self, registry: InstrumentationRegistry) -> None:
        self.registry = registry
        self._instrumentation_locks: dict[str, Lock] = {}
        self._locks_lock = Lock()
        set_registry(registry)

    def _get_lock(self, qualified_name: str) -> Lock:
        with self._locks_lock:
            if qualified_name not in self._instrumentation_locks:
                self._instrumentation_locks[qualified_name] = Lock()
            return self._instrumentation_locks[qualified_name]

    def instrument(self, qualified_name: str, func: FunctionType) -> bool:
        """Instrument a function with SCA detection hook.

        Returns:
            True if instrumentation succeeded, False otherwise.
        """
        with self._get_lock(qualified_name):
            if self.registry.is_instrumented(qualified_name):
                log.debug("Already instrumented: %s", qualified_name)
                return True

            try:
                if not self.registry.has_target(qualified_name):
                    self.registry.add_target(qualified_name, pending=False)

                original_code = func.__code__
                # AIDEV-NOTE: co_firstlineno is the `def` line, but on
                # Python <3.11 the bytecode instructions start on the first
                # body line (the line after `def`).  Use the first real
                # instruction line so inject_hook can find a matching line.
                first_line = _first_instr_line(original_code)

                inject_hook(func, sca_detection_hook, first_line, qualified_name)

                self.registry.mark_instrumented(qualified_name, original_code)

                log.debug("Instrumented: %s at line %d", qualified_name, first_line)
                return True

            except Exception:
                log.debug("Failed to instrument %s", qualified_name, exc_info=True)
                return False


def apply_instrumentation_updates(targets: list[dict]) -> None:
    """Apply instrumentation updates from CVE data or Remote Configuration.

    For each target:
    - If the module is already imported -> resolve, register, and instrument now.
    - If the module is NOT imported -> register as pending and hook into
      ModuleWatchdog so instrumentation happens automatically when the
      module is imported later.

    Args:
        targets: List of dicts with keys: target, dependency_name, cve_ids.
    """
    try:
        from ddtrace.appsec.sca._registry import get_global_registry

        registry = get_global_registry()
        instrumenter = get_instrumenter(registry)

        _process_additions(instrumenter, registry, targets)

    except Exception:
        log.debug("Fatal error in apply_instrumentation_updates", exc_info=True)


def _process_additions(instrumenter: Instrumenter, registry: InstrumentationRegistry, targets: list[dict]) -> None:
    """Process target additions: resolve and instrument immediately if possible.

    AIDEV-NOTE: We only instrument targets whose modules are already imported.
    Lazy instrumentation via ModuleWatchdog was removed because installing
    ModuleWatchdog wraps the import system for ALL subsequent module loads,
    which causes RecursionError in ssl.SSLContext.options during requests/
    urllib3 import chains.  CVEs for not-yet-imported modules are still
    registered with reached=[] on telemetry so the backend knows the
    vulnerability exists; only the runtime reachability hit is deferred.
    """
    from ddtrace.appsec.sca._resolver import SymbolResolver

    for target_info in targets:
        target_name = target_info["target"]
        dep_name = target_info.get("dependency_name", "")
        cve_ids = target_info.get("cve_ids", [])

        try:
            if registry.has_target(target_name) and registry.is_instrumented(target_name):
                log.debug("Target already instrumented: %s", target_name)
                continue

            # Register target with CVE info (even if not yet resolvable)
            if not registry.has_target(target_name):
                registry.add_target(
                    target_name,
                    pending=True,
                    package_name=dep_name,
                    cve_ids=cve_ids,
                )

            # Try to resolve and instrument now
            result = SymbolResolver.resolve(target_name)
            if result:
                _, func = result
                instrumenter.instrument(target_name, func)
            else:
                log.debug("Module not yet imported, skipping instrumentation: %s", target_name)

        except Exception as e:
            log.debug("Failed to process target %s: %s", target_name, e, exc_info=True)


# AIDEV-NOTE: _register_lazy_hook was removed because installing ModuleWatchdog
# hooks wraps the import system globally, causing RecursionError in
# ssl.SSLContext.options during requests/urllib3 import chains.
# A safer lazy instrumentation mechanism (e.g., polling sys.modules on heartbeat)
# can be added later if needed for modules imported after post_preload().
