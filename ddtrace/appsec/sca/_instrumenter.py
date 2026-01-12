"""Bytecode instrumentation for SCA detection.

This module provides functionality to instrument Python functions at runtime using
bytecode injection. It integrates with dd-trace-py's bytecode_injection infrastructure.
"""

from types import FunctionType
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


def set_registry(registry: "InstrumentationRegistry") -> None:  # noqa: F821
    """Set global registry reference.

    Args:
        registry: InstrumentationRegistry instance to use for recording hits
    """
    global _registry
    _registry = registry


def sca_detection_hook(qualified_name: str) -> None:
    """Hook injected into instrumented functions.

    This function is called at the entry point of every instrumented function.
    It records the hit in the registry, adds span tags for observability,
    and sends telemetry metrics.

    Args:
        qualified_name: Fully qualified name of the instrumented function
    """
    if _registry:
        # Record hit in registry
        _registry.record_hit(qualified_name)

        # Add span tags for observability
        span = core.get_span()
        if span:
            # Mark span as having SCA instrumentation
            span._set_tag_str(SCA.TAG_INSTRUMENTED, "true")
            # Mark span as having a detection hit
            span._set_tag_str(SCA.TAG_DETECTION_HIT, "true")
            # Tag with the specific target that was hit
            span._set_tag_str(SCA.TAG_TARGET, qualified_name)

        # Send telemetry metric for detection hit
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.APPSEC, "sca.detection.hook_hits", 1, tags=(("target", qualified_name),)
        )

        # TODO: Add vulnerability detection logic here
        # - Check if function execution indicates a known vulnerability
        # - Send additional telemetry for specific vulnerabilities


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
        set_registry(registry)

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
        # Check if already instrumented
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

            # Send telemetry for successful instrumentation
            telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.instrumentation_success", 1)

            log.info("Instrumented: %s at line %d", qualified_name, first_line)
            return True

        except Exception:
            log.error("Failed to instrument %s", qualified_name, exc_info=True)

            # Send telemetry for failed instrumentation
            telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.instrumentation_errors", 1)

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

    Args:
        targets_to_add: New targets to instrument (qualified names)
        targets_to_remove: Existing targets to uninstrument (qualified names)
    """
    from ddtrace.appsec.sca._registry import get_global_registry
    from ddtrace.appsec.sca._resolver import LazyResolver
    from ddtrace.appsec.sca._resolver import SymbolResolver

    registry = get_global_registry()
    resolver = SymbolResolver()
    lazy_resolver = LazyResolver()
    instrumenter = Instrumenter(registry)

    # Process removals
    for target in targets_to_remove:
        log.debug("Processing removal: %s", target)
        instrumenter.uninstrument(target)
        registry.remove_target(target)

    # Process additions
    for target in targets_to_add:
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

    # Log summary and send telemetry
    if targets_to_add or targets_to_remove:
        stats = registry.get_stats()
        instrumented_count = sum(1 for s in stats.values() if s["is_instrumented"])
        pending_count = sum(1 for s in stats.values() if s["is_pending"])
        total_count = len(stats)

        log.info("Instrumentation update complete: %d instrumented, %d pending", instrumented_count, pending_count)

        # Send gauge metrics for current state
        telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.targets_total", total_count)
        telemetry_writer.add_gauge_metric(
            TELEMETRY_NAMESPACE.APPSEC, "sca.detection.targets_instrumented", instrumented_count
        )
        telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.targets_pending", pending_count)

    # TODO: Hook into ModuleWatchdog for lazy instrumentation
    # When modules are imported, retry pending targets:
    # @ModuleWatchdog.after_module_imported(module_name)
    # def retry_instrumentation(module):
    #     resolved = lazy_resolver.retry_pending()
    #     for qname, func in resolved:
    #         registry.mark_pending_resolved(qname)
    #         instrumenter.instrument(qname, func)
