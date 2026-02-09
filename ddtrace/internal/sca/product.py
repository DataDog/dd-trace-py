"""SCA product lifecycle hooks.

This module manages the lifecycle of the SCA (Software Composition Analysis) detection feature,
which enables runtime instrumentation of customer code for vulnerability detection.
"""

from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config as tracer_config
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)

# Product dependencies - requires Remote Configuration to be available
requires = ["remote-configuration"]


def start():
    """Initialize SCA detection when enabled.

    Checks both configuration flags:
    - DD_APPSEC_SCA_ENABLED: Main SCA product opt-in (billing/licensing)
    - DD_SCA_DETECTION_ENABLED: Runtime detection feature flag

    Both must be enabled for SCA detection to be activated.
    """
    # Check both flags:
    # - tracer_config._sca_enabled: Main SCA product (DD_APPSEC_SCA_ENABLED)
    # - asm_config._sca_detection_enabled: Runtime detection feature (DD_SCA_DETECTION_ENABLED)
    if tracer_config._sca_enabled and asm_config._sca_detection_enabled:
        try:
            from ddtrace.appsec.sca import enable_sca_detection

            success = enable_sca_detection()
            if success:
                log.info("SCA detection started")
            else:
                log.error("SCA detection failed to start (enable returned False)")
        except ImportError as e:
            # Missing dependencies - log but don't crash tracer
            log.error("SCA detection unavailable: missing dependencies - %s", e, exc_info=True)
        except Exception as e:
            # Unexpected error
            log.error("Failed to start SCA detection: unexpected error - %s", e, exc_info=True)


def stop(join: bool = False) -> None:
    """Cleanup SCA detection on shutdown.

    Args:
        join: If True, wait for background operations to complete.
              If False (default), shutdown is immediate.
    """
    try:
        from ddtrace.appsec.sca import disable_sca_detection

        success = disable_sca_detection()
        if success:
            log.info("SCA detection stopped")
        else:
            log.warning("SCA detection failed to stop cleanly")
    except Exception as e:
        log.error("Failed to stop SCA detection: %s", e, exc_info=True)


def restart(join: bool = False) -> None:
    """Handle fork scenarios by restarting SCA detection in child process.

    SCA detection state is process-local, so we need to restart in child processes.

    Args:
        join: If True, wait for background operations to complete during stop.
              If False (default), restart happens immediately without waiting.
    """
    stop(join=join)
    start()
