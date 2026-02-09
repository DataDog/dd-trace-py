"""Bytecode instrumentation for SCA detection.

This module provides functionality to instrument Python functions at runtime using
bytecode injection. It integrates with dd-trace-py's bytecode_injection infrastructure.
"""

import os
from threading import Lock
from types import FunctionType
from typing import Dict
from typing import Optional

from ddtrace.appsec._constants import SCA
from ddtrace.internal import core
from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


# Global reference to registry (set during initialization)
_registry: Optional["InstrumentationRegistry"] = None  # noqa: F821
_registry_lock = Lock()


def _reset_registry_after_fork():
    """Reset module-level registry reference after fork."""
    global _registry
    _registry = None
    log.debug("Reset _instrumenter module registry reference after fork")


# Register fork handler for process safety
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_registry_after_fork)


def set_registry(registry: "InstrumentationRegistry") -> None:  # noqa: F821
    """Set global registry reference.

    Args:
        registry: InstrumentationRegistry instance to use for recording hits
    """
    global _registry
    with _registry_lock:
        _registry = registry


def sca_detection_hook(qualified_name: str) -> None:
    """Hook injected into instrumented functions.

    This function is called at the entry point of every instrumented function.
    It records the hit in the registry, adds span tags for observability,
    and sends telemetry metrics.

    CRITICAL: This hook runs in customer code and MUST NOT throw exceptions.
    All operations are wrapped in try-except to ensure customer code is never broken.

    Args:
        qualified_name: Fully qualified name of the instrumented function
    """
    try:
        # Fast path: acquire lock briefly to get registry reference
        with _registry_lock:
            registry_ref = _registry
        if not registry_ref:
            return

        # Record hit in registry - wrapped for safety
        try:
            registry_ref.record_hit(qualified_name)
        except Exception:
            # Silent failure - don't break customer code
            # Error is logged at DEBUG level to avoid noise
            log.debug("Failed to record hit for %s", qualified_name, exc_info=True)

        # Add span tags for observability - wrapped for safety
        # OPTIMIZATION: Set static tags only once per span, update dynamic target on each call
        try:
            span = core.get_span()
            if span:
                # Check if static tags already set on this span to avoid redundant operations
                if not span._get_tag_str(SCA.TAG_INSTRUMENTED):
                    # First hit in this span - set all static tags once
                    span._set_tag_str(SCA.TAG_INSTRUMENTED, "true")
                    span._set_tag_str(SCA.TAG_DETECTION_HIT, "true")
                # Always update the dynamic target tag (may change for different functions in span)
                span._set_tag_str(SCA.TAG_TARGET, qualified_name)
        except Exception:
            # Silent failure - don't break customer code
            log.debug("Failed to add span tags for %s", qualified_name, exc_info=True)

        # Send telemetry metric for detection hit - wrapped for safety (RFC Section 7 line 148)
        # OPTIMIZATION: Sample telemetry at 1% to reduce overhead (hit counts still tracked in registry)
        try:
            import random

            if random.random() < 0.01:  # 1% sampling
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.APPSEC, "sca.detections", 1, tags=(("target", qualified_name),)
                )
        except Exception:
            # Silent failure - don't break customer code
            log.debug("Failed to send telemetry for %s", qualified_name, exc_info=True)

        # TODO(APPSEC-XXXX): Add vulnerability detection logic here
        # Priority: Medium - Required for full SCA detection feature
        # - Check if function execution indicates a known vulnerability
        # - Send additional telemetry for specific vulnerabilities
        # - Match against vulnerability database from RC payload

    except Exception:
        # Ultimate safety net - NEVER let exceptions escape to customer code
        # Log at DEBUG to avoid noise from expected errors
        log.debug("Unexpected error in SCA detection hook for %s", qualified_name, exc_info=True)


class Instrumenter:
    """Manages bytecode instrumentation of target functions.

    This class handles the application of bytecode patches using dd-trace-py's
    inject_hook() API. It works with the InstrumentationRegistry to track
    instrumentation state.
    """

    def __init__(self, registry: "InstrumentationRegistry"):  # noqa: F821
        """Initialize instrumenter with state registry.

        Args:
            registry: InstrumentationRegistry for tracking instrumentation state
        """
        self.registry = registry
        self._instrumentation_locks: Dict[str, Lock] = {}
        self._locks_lock = Lock()
        set_registry(registry)

    def _get_lock(self, qualified_name: str) -> Lock:
        """Get or create lock for a specific target to prevent concurrent instrumentation.

        Args:
            qualified_name: Fully qualified name of target

        Returns:
            Lock instance for this specific target
        """
        with self._locks_lock:
            if qualified_name not in self._instrumentation_locks:
                self._instrumentation_locks[qualified_name] = Lock()
            return self._instrumentation_locks[qualified_name]

    def instrument(self, qualified_name: str, func: FunctionType) -> bool:
        """Instrument a function with SCA detection hook.

        Applies bytecode patching to inject the SCA detection hook at the
        function entry point. The hook will be called every time the
        instrumented function is invoked.

        Args:
            qualified_name: Fully qualified name (e.g., "module.path:function")
            func: Function object to instrument

        Returns:
            True if instrumentation succeeded, False otherwise
        """
        # Acquire per-function lock to prevent concurrent instrumentation of same target
        with self._get_lock(qualified_name):
            # Check if already instrumented (within lock to prevent race)
            if self.registry.is_instrumented(qualified_name):
                log.debug("Already instrumented: %s", qualified_name)
                return True

            try:
                # Ensure target is in registry (if not already added by caller)
                if not self.registry.has_target(qualified_name):
                    self.registry.add_target(qualified_name, pending=False)

                # Store original code before patching
                original_code = func.__code__

                # Get first line number for injection point
                first_line = original_code.co_firstlineno

                # Inject hook at function entry
                # inject_hook modifies func.__code__ in place
                inject_hook(func, sca_detection_hook, first_line, qualified_name)

                # Mark as instrumented in registry
                self.registry.mark_instrumented(qualified_name, original_code)

                # Send telemetry for successful instrumentation (RFC Section 7 line 145)
                telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.instrumentations", 1)

                log.info("Instrumented: %s at line %d", qualified_name, first_line)
                return True

            except Exception:
                log.error("Failed to instrument %s", qualified_name, exc_info=True)

                # Send telemetry for failed instrumentation (RFC Section 7 line 147)
                telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.instrumentation_failed", 1)

                return False

    def uninstrument(self, qualified_name: str) -> bool:
        """Remove instrumentation from a function.

        Note: Currently not implemented as dd-trace-py's bytecode injection
        does not provide an eject_hook() API. Once available, this will
        restore the original __code__ object.

        Args:
            qualified_name: Fully qualified name of function to uninstrument

        Returns:
            False (not yet implemented)
        """
        log.warning("Uninstrumentation not yet implemented: %s", qualified_name)
        return False


def apply_instrumentation_updates(targets_to_add: list[str], targets_to_remove: list[str]) -> None:
    """Apply instrumentation updates from Remote Configuration.

    This function is called when RC sends updated target lists. It processes
    additions and removals, resolving symbols and applying bytecode patches.

    Each target is processed independently - one failure does not stop the batch.

    Args:
        targets_to_add: New targets to instrument (qualified names)
        targets_to_remove: Existing targets to uninstrument (qualified names)
    """
    try:
        from ddtrace.appsec.sca._registry import get_global_registry
        from ddtrace.appsec.sca._resolver import LazyResolver
        from ddtrace.appsec.sca._resolver import SymbolResolver

        registry = get_global_registry()
        resolver = SymbolResolver()
        lazy_resolver = LazyResolver()
        instrumenter = Instrumenter(registry)

        # Process removals - don't let one failure stop others
        for target in targets_to_remove:
            try:
                log.debug("Processing removal: %s", target)

                # Attempt uninstrumentation first
                uninstrument_success = instrumenter.uninstrument(target)

                # Only remove from registry if uninstrumentation succeeded or wasn't needed
                if uninstrument_success or not registry.is_instrumented(target):
                    registry.remove_target(target)
                    log.info("Successfully removed target: %s", target)
                else:
                    log.warning("Uninstrumentation failed for %s, keeping in registry", target)
                    telemetry_writer.add_count_metric(
                        TELEMETRY_NAMESPACE.APPSEC,
                        "sca.removal_failures",
                        1,
                        tags=(("reason", "uninstrumentation_failed"),),
                    )

            except Exception as e:
                log.error("Failed to remove target %s: %s", target, e, exc_info=True)
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.APPSEC, "sca.removal_failures", 1, tags=(("reason", "exception"),)
                )
                continue  # Keep processing other removals

        # Process additions - don't let one failure stop others
        for target in targets_to_add:
            try:
                # Skip if already tracked
                if registry.has_target(target):
                    log.debug("Target already tracked: %s", target)
                    continue

                # Attempt resolution
                result = resolver.resolve(target)
                if result:
                    qualified_name, func = result
                    registry.add_target(qualified_name, pending=False)
                    success = instrumenter.instrument(qualified_name, func)
                    if success:
                        log.info("Successfully instrumented: %s", qualified_name)
                    else:
                        log.warning("Instrumentation failed: %s", qualified_name)
                else:
                    # Module not yet imported, add to lazy resolution
                    registry.add_target(target, pending=True)
                    lazy_resolver.add_pending(target)
                    log.debug("Deferred instrumentation (module not loaded): %s", target)

            except Exception as e:
                log.error("Failed to process target %s: %s", target, e, exc_info=True)
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.APPSEC, "sca.processing_failures", 1, tags=(("operation", "add_target"),)
                )
                continue  # Keep processing other additions

        # Log summary and send telemetry - wrapped for safety
        if targets_to_add or targets_to_remove:
            try:
                stats = registry.get_stats()
                # OPTIMIZATION: Single-pass counting instead of multiple comprehensions
                instrumented_count = 0
                pending_count = 0
                for s in stats.values():
                    if s["is_instrumented"]:
                        instrumented_count += 1
                    if s["is_pending"]:
                        pending_count += 1
                total_count = len(stats)

                log.info(
                    "Instrumentation update complete: %d instrumented, %d pending", instrumented_count, pending_count
                )

                # Send gauge metrics for current state (RFC Section 7)
                telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.targets_total", total_count)
                telemetry_writer.add_gauge_metric(
                    TELEMETRY_NAMESPACE.APPSEC, "sca.targets_instrumented", instrumented_count
                )
                telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.pending_targets", pending_count)
            except Exception:
                log.error("Failed to send telemetry metrics", exc_info=True)

    except Exception:
        # Catch-all to prevent RC processing from dying
        log.error("Fatal error in apply_instrumentation_updates", exc_info=True)

    # TODO(APPSEC-XXXX): Hook into ModuleWatchdog for lazy instrumentation
    # Priority: High - Enables automatic instrumentation of late-loaded modules
    # Design: When modules are imported, automatically retry pending targets
    # See: ddtrace/internal/module.py ModuleWatchdog class
    # Implementation approach:
    #   @ModuleWatchdog.after_module_imported(module_name)
    #   def retry_instrumentation(module):
    #       resolved = lazy_resolver.retry_pending()
    #       for qname, func in resolved:
    #           instrumenter.instrument(qname, func)
