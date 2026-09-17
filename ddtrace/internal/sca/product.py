"""SCA product lifecycle hooks.

Manages the lifecycle of the SCA detection feature, which enables runtime
instrumentation of customer code for vulnerability detection.

At startup (when DD_APPSEC_SCA_ENABLED=true):
1. Activates SCA metadata on tracked dependencies (metadata: []).
2. post_preload: after ddtrace integrations finish patching, loads the
   static CVE JSON and calls apply_instrumentation_updates which:
   - Instruments targets whose modules are already imported.
   - Registers ModuleWatchdog hooks for targets whose modules are not yet
     imported, so they get instrumented lazily on first import.
"""

from ddtrace.internal.logger import get_logger
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config


log = get_logger(__name__)

requires: list[str] = []


def enabled() -> bool:
    return bool(appsec_telemetry_config.SCA_ENABLED) and not in_aws_lambda()


def _get_installed_packages() -> dict[str, str]:
    """Build a dict of {package_name: version} from all installed distributions.

    Uses importlib.metadata directly (not the telemetry writer) so we can
    discover ALL installed packages at startup, before any are imported.
    """
    try:
        from importlib.metadata import distributions

        installed_packages: dict[str, str] = {}
        for dist in distributions():
            name = dist.metadata["Name"]
            if name:
                installed_packages[name.lower()] = dist.version
        return installed_packages
    except Exception:
        log.debug("Could not enumerate installed packages", exc_info=True)
        return {}


def _load_and_instrument(after_fork: bool = False) -> None:
    """Load CVE targets from static JSON and instrument applicable functions.

    Uses apply_instrumentation_updates which handles both immediate
    instrumentation (module already imported) and deferred instrumentation
    via ModuleWatchdog (module not yet imported).

    Args:
        after_fork: If True, skip bytecode injection for already-imported
            targets since they inherit injected hooks from the parent
            process.  Only populate the registry so existing hooks work.
    """
    from ddtrace.appsec.sca._cve_loader import load_cve_targets
    from ddtrace.appsec.sca._instrumenter import apply_instrumentation_updates

    installed = _get_installed_packages()
    if not installed:
        log.debug("No installed packages found — skipping CVE target loading")
        return

    applicable = load_cve_targets(installed)
    if not applicable:
        log.debug("No applicable CVE targets for installed packages")
        return

    log.info(
        "SCA: loading %d targets for %d CVEs",
        sum(len(entry["targets"]) for entry in applicable),
        len(applicable),
    )

    # Register all CVEs immediately so they appear
    # with reached:[] on the next heartbeat, before any symbol is executed.
    # Deduplication is handled by DependencyEntry.add_metadata (idempotent).
    from ddtrace.internal.telemetry import telemetry_writer

    for entry in applicable:
        telemetry_writer.register_cve_metadata(entry["dependency_name"], entry["id"])

    apply_instrumentation_updates(targets=applicable, after_fork=after_fork)


def post_preload() -> None:
    """Load CVE data and instrument targets after ddtrace integrations finish patching.

    This runs after patch_all() completes (called from
    sitecustomize.py post_preload callbacks).  At this point, ddtrace's
    monkey-patching (wrapt FunctionWrapper) is in place, so the resolver
    correctly unwraps __wrapped__ to get the original function for
    inject_hook.  Modules not yet imported get ModuleWatchdog hooks for
    lazy instrumentation.
    """
    if not enabled():
        return

    try:
        _load_and_instrument()
    except Exception:
        log.debug("Failed SCA post_preload instrumentation", exc_info=True)


def start() -> None:
    """Initialize SCA detection when DD_APPSEC_SCA_ENABLED=true.

    Enables SCA metadata on tracked dependencies (metadata: []).
    Actual instrumentation happens in post_preload() after integrations
    are patched.
    """
    if not enabled():
        return

    try:
        from ddtrace.internal.telemetry import telemetry_writer

        telemetry_writer.enable_sca_metadata()
        log.debug("SCA detection started")
    except Exception as e:
        log.debug("Failed to start SCA detection: %s", e, exc_info=True)


def stop(join: bool = False) -> None:
    """Cleanup SCA detection on shutdown."""
    log.debug("SCA detection stopped")


def restart(join: bool = False) -> None:
    """Handle fork scenarios by restarting SCA detection in child process.

    We explicitly reset the instrumenter and registry state here
    instead of relying on os.register_at_fork(after_in_child=...).  The
    CPython fork callbacks run AFTER ddtrace's forksafe mechanism calls
    restart(), so if we relied on register_at_fork, the cleanup would wipe
    the state we just set up.  See system_tests_error.md for the full
    debugging trace.
    """
    if not enabled():
        return

    stop(join=join)

    # Reset instrumenter and registry state BEFORE re-initializing.
    # This must happen here (not in register_at_fork) to ensure correct ordering.
    from ddtrace.appsec.sca._instrumenter import _reset_after_fork as reset_instrumenter
    from ddtrace.appsec.sca._registry import _reset_global_registry_after_fork as reset_registry

    reset_instrumenter()
    reset_registry()

    try:
        start()
    except Exception:
        log.debug("Failed SCA start after restart", exc_info=True)
        return

    try:
        _load_and_instrument(after_fork=True)
    except Exception:
        log.debug("Failed SCA instrumentation after restart", exc_info=True)
