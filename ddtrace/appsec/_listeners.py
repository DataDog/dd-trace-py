import sys

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)


def _report_asm_enabled() -> None:
    """Report the current DD_APPSEC_ENABLED state to instrumentation telemetry.

    ASM enablement can change at runtime (startup product load or a remote-config
    one-click toggle), so telemetry is refreshed wherever the state changes. The native
    telemetry worker stores the latest configuration and re-sends it on its heartbeat, so
    a single change-driven report keeps telemetry in sync.
    """
    try:
        from ddtrace.appsec._constants import APPSEC
        from ddtrace.internal.telemetry import telemetry_writer

        telemetry_writer.add_configuration(
            APPSEC.ENV,
            int(asm_config._asm_enabled),
            asm_config.asm_enabled_origin,
        )
    except Exception:
        log.debug("Could not report appsec_enabled telemetry config status", exc_info=True)


def _abort_appsec(failure_msg: str) -> None:
    """Disable AppSec and prevent it from being enabled through remote configuration

    This is called in case of non-recoverable AppSec load-time failure, such as a libddwaf loading error.
    """
    log.warning("Disabling AppSec: libddwaf failed to load (%s)", failure_msg or "unknown error")

    if asm_config._asm_enabled:
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)

    asm_config._asm_enabled = False
    asm_config._asm_can_be_enabled = False
    asm_config._asm_libddwaf_available = False
    asm_config._asm_rc_enabled = False
    asm_config._load_modules = False
    asm_config._ddwaf_version = "error"

    from ddtrace.appsec._remoteconfiguration import disable_appsec_rc

    disable_appsec_rc()

    core.dispatch("asm.tracer.reconfigure", (False, ""), allow_raise=True)
    _report_asm_enabled()


def disable_appsec(reconfigure_tracer: bool = False) -> None:
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
    except Exception as e:
        _abort_appsec(str(e))
        return

    AppSecSpanProcessor.disable()

    if asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.disable()

    asm_config._asm_enabled = False
    if reconfigure_tracer:
        core.dispatch("asm.tracer.reconfigure", (False, ""), allow_raise=True)

    _report_asm_enabled()
    return


def load_appsec(reconfigure_tracer: bool = False, origin: str = "") -> bool:
    """Lazily load the appsec module listeners."""
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
    except Exception as e:
        _abort_appsec(str(e))
        return False

    AppSecSpanProcessor.enable()
    if asm_config._api_security_enabled and not asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.enable()

    asm_config._asm_enabled = True
    if reconfigure_tracer:
        if origin:
            asm_config._asm_enabled_origin = origin  # type: ignore[assignment]
        core.dispatch("asm.tracer.reconfigure", (True, origin), allow_raise=True)

    _report_asm_enabled()
    return True


def load_common_appsec_modules() -> None:
    """Lazily load the common module patches."""
    from ddtrace.internal.settings.asm import config as asm_config

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# Test only helpers
# for tests that needs to load the appsec module later
core.on("test.config.override", load_common_appsec_modules)


def _asm_switch_state() -> None:
    from ddtrace.internal.appsec import product

    if asm_config._asm_enabled:
        load_appsec()
        product.register_subscribers()
    elif "ddtrace.appsec._processor" in sys.modules:
        disable_appsec()
        product.unregister_subscribers()
