"""SCA detection runtime instrumentation."""

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)

_sca_detection_enabled = False


def enable_sca_detection() -> None:
    """Enable SCA detection and register with Remote Configuration."""
    global _sca_detection_enabled

    if _sca_detection_enabled:
        log.debug("SCA detection already enabled")
        return

    try:
        from ddtrace.appsec.sca._registry import get_global_registry
        from ddtrace.appsec.sca._remote_config import enable_sca_detection_rc

        # Initialize registry (creates singleton if needed)
        get_global_registry()

        # Enable RC subscription
        enable_sca_detection_rc()

        _sca_detection_enabled = True

        # Send telemetry for enabled state
        telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.enabled", 1)

        log.info("SCA detection enabled")

    except Exception:
        log.error("Failed to enable SCA detection", exc_info=True)


def disable_sca_detection() -> None:
    """Disable SCA detection and unregister from Remote Configuration."""
    global _sca_detection_enabled

    if not _sca_detection_enabled:
        return

    try:
        from ddtrace.appsec.sca._remote_config import disable_sca_detection_rc

        # Unregister from RC
        disable_sca_detection_rc()

        # TODO: Optionally uninstrument all targets

        _sca_detection_enabled = False

        # Send telemetry for disabled state
        telemetry_writer.add_gauge_metric(TELEMETRY_NAMESPACE.APPSEC, "sca.detection.enabled", 0)

        log.info("SCA detection disabled")

    except Exception:
        log.error("Failed to disable SCA detection", exc_info=True)
