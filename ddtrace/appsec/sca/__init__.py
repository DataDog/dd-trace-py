"""SCA detection runtime instrumentation."""

from threading import Lock

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)

_sca_detection_enabled = False
_sca_enable_lock = Lock()


def enable_sca_detection() -> bool:
    """Enable SCA detection and register with Remote Configuration.

    Returns:
        True if enabled successfully, False otherwise
    """
    global _sca_detection_enabled

    with _sca_enable_lock:
        if _sca_detection_enabled:
            log.debug("SCA detection already enabled")
            return True

        try:
            from ddtrace.appsec.sca._registry import get_global_registry
            from ddtrace.appsec.sca._remote_config import enable_sca_detection_rc

            # Initialize registry (creates singleton if needed)
            get_global_registry()

            # Enable RC subscription
            enable_sca_detection_rc()

            _sca_detection_enabled = True

            # Send telemetry for enabled state (RFC Section 7)
            telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.enabled", 1)

            log.info("SCA detection enabled")
            return True

        except ImportError as e:
            # Module import failed - dependency missing
            log.error("Failed to enable SCA detection: missing dependencies - %s", e, exc_info=True)
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, "sca.startup_failures", 1, tags=(("reason", "missing_dependencies"),)
            )
            return False
        except Exception as e:
            # Unexpected error
            log.error("Failed to enable SCA detection: unexpected error - %s", e, exc_info=True)
            telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, "sca.startup_failures", 1, tags=(("reason", "unexpected"),)
            )
            return False


def disable_sca_detection() -> bool:
    """Disable SCA detection and unregister from Remote Configuration.

    Returns:
        True if disabled successfully, False otherwise
    """
    global _sca_detection_enabled

    with _sca_enable_lock:
        if not _sca_detection_enabled:
            return True

        try:
            from ddtrace.appsec.sca._remote_config import disable_sca_detection_rc

            # Unregister from RC
            disable_sca_detection_rc()

            # TODO(APPSEC-XXXX): Optionally uninstrument all targets
            # Currently bytecode patches remain in place after disable.
            # This requires bytecode_injection.eject_hook() API (not yet available).

            _sca_detection_enabled = False

            # Send telemetry for disabled state (RFC Section 7)
            telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.enabled", 0)

            log.info("SCA detection disabled")
            return True

        except Exception as e:
            log.error("Failed to disable SCA detection: %s", e, exc_info=True)
            return False
