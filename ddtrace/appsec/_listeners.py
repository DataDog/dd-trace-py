from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.trace import tracer


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
    from ddtrace.trace import tracer

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

    tracer.configure(appsec_enabled=False)
    _report_asm_enabled()


def disable_appsec(reconfigure_tracer: bool = False) -> None:
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
        from ddtrace.appsec._processor import unlisten as processor_unlisten
    except Exception as e:
        _abort_appsec(str(e))
        return

    AppSecSpanProcessor.disable()
    processor_unlisten()

    from ddtrace.appsec._asm_request_context import asm_unlisten
    from ddtrace.appsec._contrib.aws_lambda import unlisten as aws_lambda_unlisten
    from ddtrace.appsec._contrib.django import unlisten as django_unlisten
    from ddtrace.appsec._contrib.fastapi import unlisten as fastapi_unlisten
    from ddtrace.appsec._contrib.flask import unlisten as flask_unlisten
    from ddtrace.appsec._contrib.httpx import unlisten as httpx_unlisten
    from ddtrace.appsec._contrib.openai.handlers import unlisten as openai_unlisten
    from ddtrace.appsec._contrib.stripe.handlers import unlisten as stripe_unlisten
    from ddtrace.appsec._contrib.tornado import unlisten as tornado_unlisten
    from ddtrace.appsec._handlers import unlisten
    from ddtrace.appsec._trace_utils import unlisten as trace_utils_unlisten

    unlisten()
    asm_unlisten()
    aws_lambda_unlisten()
    flask_unlisten()
    django_unlisten()
    fastapi_unlisten()
    httpx_unlisten()
    openai_unlisten()
    stripe_unlisten()
    tornado_unlisten()
    trace_utils_unlisten()

    if asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.disable()

    if reconfigure_tracer:
        tracer.configure(appsec_enabled=False)
    else:
        asm_config._asm_enabled = False

    _report_asm_enabled()
    return


def load_appsec(reconfigure_tracer: bool = False, origin: str = "") -> bool:
    """Lazily load the appsec module listeners."""
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor
        from ddtrace.appsec._processor import listen as processor_listen
    except Exception as e:
        _abort_appsec(str(e))
        return False

    from ddtrace.appsec._asm_request_context import asm_listen
    from ddtrace.appsec._contrib.aws_lambda import listen as aws_lambda_listen
    from ddtrace.appsec._contrib.django import listen as django_listen
    from ddtrace.appsec._contrib.fastapi import listen as fastapi_listen
    from ddtrace.appsec._contrib.flask import listen as flask_listen
    from ddtrace.appsec._contrib.httpx import listen as httpx_listen
    from ddtrace.appsec._contrib.openai.handlers import listen as openai_listen
    from ddtrace.appsec._contrib.stripe.handlers import listen as stripe_listen
    from ddtrace.appsec._contrib.tornado import listen as tornado_listen
    from ddtrace.appsec._handlers import listen
    from ddtrace.appsec._handlers import listen_telemetry
    from ddtrace.appsec._trace_utils import listen as trace_utils_listen
    # from ddtrace.appsec._contrib.grpc import listen as grpc_listen

    listen()
    listen_telemetry()
    asm_listen()
    aws_lambda_listen()
    flask_listen()
    django_listen()
    fastapi_listen()
    httpx_listen()
    openai_listen()
    stripe_listen()
    tornado_listen()
    trace_utils_listen()
    processor_listen()

    # GRPC integration was disabled in commit 5fe1c163738c9e6d13127067f8eceee2302bcb67, deemed too unreliable
    # grpc_listen()

    from ddtrace.appsec._processor import AppSecSpanProcessor

    AppSecSpanProcessor.enable()
    if asm_config._api_security_enabled and not asm_config._api_security_active:
        from ddtrace.appsec._api_security.api_manager import APIManager

        APIManager.enable()

    if reconfigure_tracer:
        tracer.configure(appsec_enabled=True, appsec_enabled_origin=origin)
    else:
        asm_config._asm_enabled = True

    _report_asm_enabled()
    return True


def load_common_appsec_modules() -> None:
    """Lazily load the common module patches."""
    from ddtrace.internal.settings.asm import config as asm_config

    if asm_config._load_modules:
        from ddtrace.appsec._common_module_patches import patch_common_modules

        patch_common_modules()


# Test-only helper for tests that need to load AppSec modules later.
core.on("test.config.override", load_common_appsec_modules)
