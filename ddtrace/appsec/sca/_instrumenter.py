"""Bytecode instrumentation for SCA detection.

Provides functionality to instrument Python functions at runtime using
bytecode injection via dd-trace-py's bytecode_injection infrastructure.
"""

import os
from threading import Lock
from types import FunctionType
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


# AIDEV-NOTE: _registry is read on every hook invocation (hot path).
# We do NOT acquire a lock for reads — Python's GIL makes reference reads
# safe, and the reference is only set once at startup via set_registry().
_registry: Optional["InstrumentationRegistry"] = None  # noqa: F821

# _telemetry_writer is lazily cached on first hook invocation to avoid
# the overhead of import-lock acquisition on every call.
_telemetry_writer = None


def _reset_after_fork():
    """Reset module-level references after fork."""
    global _registry, _telemetry_writer
    _registry = None
    _telemetry_writer = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def set_registry(registry: "InstrumentationRegistry") -> None:  # noqa: F821
    """Set global registry reference. Called once at startup."""
    global _registry
    _registry = registry


def _get_telemetry_writer():
    """Lazily cache and return the telemetry writer singleton."""
    global _telemetry_writer
    if _telemetry_writer is None:
        from ddtrace.internal.telemetry import telemetry_writer
        _telemetry_writer = telemetry_writer
    return _telemetry_writer


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
        if not target_info or not target_info.get("cve_ids") or not target_info.get("package_name"):
            return

        writer = _get_telemetry_writer()
        package_name = target_info["package_name"]
        # AIDEV-NOTE: path/method extracted from qualified_name (module.path:method)
        path, _, method = qualified_name.partition(":")
        line = target_info.get("line", 0)

        for cve_id in target_info["cve_ids"]:
            writer.attach_dependency_metadata(package_name, cve_id, True, path, method, line)

    except Exception:
        log.debug("SCA detection hook error for %s", qualified_name, exc_info=True)


class Instrumenter:
    """Manages bytecode instrumentation of target functions."""

    def __init__(self, registry: "InstrumentationRegistry"):  # noqa: F821
        self.registry = registry
        self._instrumentation_locks: Dict[str, Lock] = {}
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
                first_line = original_code.co_firstlineno

                inject_hook(func, sca_detection_hook, first_line, qualified_name)

                self.registry.mark_instrumented(qualified_name, original_code)

                log.debug("Instrumented: %s at line %d", qualified_name, first_line)
                return True

            except Exception:
                log.debug("Failed to instrument %s", qualified_name, exc_info=True)
                return False

    def uninstrument(self, qualified_name: str) -> bool:
        """Remove instrumentation from a function. Not yet implemented."""
        log.debug("Uninstrumentation not yet implemented: %s", qualified_name)
        return False


def apply_instrumentation_updates(
    targets: List[Dict],
    targets_to_remove: Optional[List[str]] = None,
) -> None:
    """Apply instrumentation updates from CVE data or Remote Configuration.

    For each target:
    - If the module is already imported -> resolve, register, and instrument now.
    - If the module is NOT imported -> register as pending and hook into
      ModuleWatchdog so instrumentation happens automatically when the
      module is imported later.

    Args:
        targets: List of dicts with keys: target, dependency_name, cve_ids.
        targets_to_remove: Optional list of qualified names to uninstrument.
    """
    try:
        from ddtrace.appsec.sca._registry import get_global_registry
        from ddtrace.appsec.sca._resolver import SymbolResolver
        from ddtrace.internal.module import ModuleWatchdog

        registry = get_global_registry()
        instrumenter = Instrumenter(registry)

        _process_removals(instrumenter, registry, targets_to_remove or [])
        _process_additions(instrumenter, registry, targets)

    except Exception:
        log.debug("Fatal error in apply_instrumentation_updates", exc_info=True)


def _process_removals(instrumenter, registry, targets_to_remove):
    """Process target removals."""
    for target_name in targets_to_remove:
        try:
            uninstrument_success = instrumenter.uninstrument(target_name)
            if uninstrument_success or not registry.is_instrumented(target_name):
                registry.remove_target(target_name)
            else:
                log.debug("Uninstrumentation failed for %s, keeping in registry", target_name)
        except Exception as e:
            log.debug("Failed to remove target %s: %s", target_name, e, exc_info=True)


def _process_additions(instrumenter, registry, targets):
    """Process target additions: resolve and instrument, or defer via ModuleWatchdog."""
    from ddtrace.appsec.sca._resolver import SymbolResolver
    from ddtrace.internal.module import ModuleWatchdog

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
                    target_name, pending=True, package_name=dep_name, cve_ids=cve_ids,
                )

            # Try to resolve and instrument now
            result = SymbolResolver.resolve(target_name)
            if result:
                _, func = result
                instrumenter.instrument(target_name, func)
            else:
                # Module not yet imported — register a ModuleWatchdog hook
                module_name, _, _ = target_name.partition(":")
                if module_name:
                    _register_lazy_hook(module_name, target_name)
                    log.debug("Deferred instrumentation via ModuleWatchdog: %s", target_name)

        except Exception as e:
            log.debug("Failed to process target %s: %s", target_name, e, exc_info=True)


def _register_lazy_hook(module_name, target_name):
    """Register a ModuleWatchdog hook to instrument target when module is imported.

    AIDEV-NOTE: Uses get_global_registry() inside the callback (not a closure
    over the registry object) so that after fork the callback picks up the
    fresh registry instead of a stale pre-fork reference.
    """
    from ddtrace.internal.module import ModuleWatchdog

    def _on_module_import(module):
        """Called when the target's module is imported."""
        from ddtrace.appsec.sca._registry import get_global_registry
        from ddtrace.appsec.sca._resolver import SymbolResolver

        registry = get_global_registry()
        if registry.is_instrumented(target_name):
            return
        result = SymbolResolver.resolve(target_name)
        if result:
            _, func = result
            instrumenter = Instrumenter(registry)
            instrumenter.instrument(target_name, func)
            log.debug("Lazy-instrumented %s after module import", target_name)

    ModuleWatchdog.register_module_hook(module_name, _on_module_import)
