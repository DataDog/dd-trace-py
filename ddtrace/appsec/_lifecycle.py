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
    tracer = core.root.get_item("tracer")

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

    core.dispatch("asm.disable_rc")

    tracer.configure(appsec_enabled=False)
    _report_asm_enabled()
