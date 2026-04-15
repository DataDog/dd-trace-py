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
from ddtrace.appsec.sca._registry import get_global_registry
from ddtrace.appsec.sca._resolver import SymbolResolver
from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.telemetry import telemetry_writer


if TYPE_CHECKING:
    from ddtrace.appsec.sca._registry import InstrumentationRegistry


log = get_logger(__name__)

# _registry is read on every hook invocation (hot path).
# We do NOT acquire a lock for reads — Python's GIL makes reference reads
# safe, and the reference is only set once at startup via set_registry().
_registry: Optional[InstrumentationRegistry] = None

# Singleton Instrumenter instance, shared across all instrumentation
# operations so per-target locks are preserved.
_instrumenter_instance: Optional[Instrumenter] = None
_instrumenter_lock = Lock()


def _first_instr_line(code: types.CodeType) -> int:
    """Return the line number of the first real bytecode instruction.

    On Python <3.11, co_firstlineno is the ``def`` line, which has no
    bytecode instructions.  inject_hook needs the first *instruction*
    line, which is the first line of the function body.

    In CPython, ``dis.Instruction.starts_line`` is typically the absolute
    line number (or ``None``).  We also defensively handle instruction
    objects that expose a ``line_number`` attribute and prefer it when
    present before falling back to ``starts_line``.
    """
    import dis

    for instr in dis.get_instructions(code):
        # Prefer explicit line_number when provided by the instruction object.
        line = getattr(instr, "line_number", None)
        if line is not None:
            return line
        # Otherwise use starts_line when it carries the absolute line number.
        if instr.starts_line is not None and isinstance(instr.starts_line, int):
            return instr.starts_line
    return code.co_firstlineno


def _get_caller_info() -> tuple[str, int, str]:
    """Walk the stack to find the first user-code caller frame.
    A symbol is the function or Class.method name of the caller
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


def _reset_after_fork() -> None:
    """Reset module-level references after fork."""
    global _registry, _instrumenter_instance, _instrumenter_lock
    _registry = None
    _instrumenter_instance = None
    _instrumenter_lock = Lock()


def set_registry(registry: InstrumentationRegistry) -> None:
    """Set global registry reference. Called once at startup."""
    global _registry
    _registry = registry


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
        # No lock needed — _registry is a simple reference read
        # protected by the GIL, and only set once at startup.
        registry_ref = _registry
        if not registry_ref:
            return

        registry_ref.record_hit(qualified_name)

        target_info = registry_ref.get_target_info(qualified_name)
        if not target_info or not target_info.cve_ids or not target_info.package_name:
            return

        caller_path, caller_line, caller_symbol = _get_caller_info()

        # If the native frame walker can't find user code (e.g.,
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
            telemetry_writer.attach_dependency_metadata(
                package_name=target_info.package_name,
                cve_id=cve_id,
                path=caller_path,
                symbol=caller_symbol,
                line=caller_line,
            )

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
                # co_firstlineno is the `def` line, but on
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


def apply_instrumentation_updates(targets: list[dict], after_fork: bool = False) -> None:
    """Apply instrumentation updates from CVE data or Remote Configuration.

    For each target:
    - If the module is already imported -> resolve, register, and instrument now.
    - If the module is NOT imported -> register as pending and hook into
      ModuleWatchdog so instrumentation happens automatically when the
      module is imported later.

    Args:
        targets: List of dicts with keys: target, dependency_name, cve_ids.
        after_fork: If True, skip bytecode injection for already-imported
            targets since they inherit injected hooks from the parent
            process.  Only populate the registry so existing hooks work.
    """
    try:
        registry = get_global_registry()
        instrumenter = get_instrumenter(registry)

        _process_additions(instrumenter, registry, targets, after_fork=after_fork)
    except Exception:
        log.debug("Fatal error in apply_instrumentation_updates", exc_info=True)


def _process_additions(
    instrumenter: Instrumenter,
    registry: InstrumentationRegistry,
    targets: list[dict],
    after_fork: bool = False,
) -> None:
    """Process target additions: resolve and instrument, or defer via ModuleWatchdog.

    Args:
        after_fork: When True, skip bytecode injection for targets whose
            modules are already imported — their code objects already
            carry the hook from the parent process.  We only populate
            the registry so the existing hooks can look up CVE info.
    """
    from ddtrace.appsec.sca._resolver import SymbolResolver

    for target_info in targets:
        target_name = target_info["target"]
        dep_name = target_info.get("dependency_name", "")
        cve_ids = target_info.get("cve_ids", [])

        try:
            if registry.has_target(target_name) and registry.is_instrumented(target_name):
                # Merge any new CVE IDs that weren't present at initial registration
                # (e.g. incremental Remote Configuration updates).
                registry.merge_cve_ids(target_name, cve_ids)
                log.debug("Target already instrumented, merged CVEs: %s", target_name)
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
                if after_fork:
                    # Bytecode already has hooks from the parent process.
                    # Just mark as instrumented so the registry is populated
                    # for the existing hooks and we don't re-inject.
                    registry.mark_instrumented(target_name, func.__code__)
                    log.debug("Registered existing instrumentation after fork: %s", target_name)
                else:
                    instrumenter.instrument(target_name, func)
            else:
                # Module not yet imported — register a ModuleWatchdog hook
                module_name, _, _ = target_name.partition(":")
                if module_name:
                    _register_lazy_hook(module_name, target_name)
                    log.debug("Deferred instrumentation via ModuleWatchdog: %s", target_name)

        except Exception:
            log.debug("Failed to process target %s", target_name, exc_info=True)


def _register_lazy_hook(module_name: str, target_name: str) -> None:
    """Register a ModuleWatchdog hook to instrument target when module is imported.

    Uses get_global_registry() inside the callback (not a closure
    over the registry object) so that after fork the callback picks up the
    fresh registry instead of a stale pre-fork reference.

    Safe with gevent because SymbolResolver.resolve() uses sys.modules.get()
    instead of importlib.import_module(), so it never triggers new imports.
    """

    def _on_module_import(module: object) -> None:
        """Called when the target's module is imported.

        CRITICAL: Must not throw — runs inside Python's import machinery.
        An unhandled exception here would crash the customer's import statement.
        """
        try:
            from ddtrace.appsec.sca._registry import get_global_registry

            registry = get_global_registry()
            if registry is None or registry.is_instrumented(target_name):
                return
            result = SymbolResolver.resolve(target_name)
            if result:
                _, func = result
                instrumenter = get_instrumenter(registry)
                instrumenter.instrument(target_name, func)
                log.debug("Lazy-instrumented %s after module import", target_name)
        except Exception:
            log.debug("Failed lazy instrumentation for %s", target_name, exc_info=True)

    ModuleWatchdog.register_module_hook(module_name, _on_module_import)
