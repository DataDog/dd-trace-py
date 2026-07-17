from ddtrace.appsec._constants import APPSEC
from ddtrace.internal.settings.asm import config


requires = ["remote-configuration"]

_agentic_onboarding_reported = False


def _report_agentic_onboarding():
    # RFC-1110: report whether AppSec came up when DD_APPSEC_AGENTIC_ONBOARDING is set. Called from
    # post_preload and start (the Lambda entrypoint); the one-shot guard avoids a duplicate.
    global _agentic_onboarding_reported
    if _agentic_onboarding_reported or config._asm_agentic_onboarding is None:
        return
    _agentic_onboarding_reported = True
    from ddtrace.internal.telemetry import telemetry_writer

    telemetry_writer.add_configuration(
        APPSEC.AGENTIC_ONBOARDING_TELEMETRY, config._asm_enabled, config.value_source(APPSEC.AGENTIC_ONBOARDING)
    )


def post_preload():
    _report_agentic_onboarding()


def enabled():
    return config._asm_enabled or config._asm_can_be_enabled or config._asm_rc_enabled


def start():
    try:
        if config._asm_enabled or config._asm_can_be_enabled:
            from ddtrace.appsec._listeners import load_common_appsec_modules

            load_common_appsec_modules()

        if config._asm_rc_enabled:
            from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

            enable_appsec_rc()

        if config._asm_enabled:
            from ddtrace.appsec._listeners import load_appsec

            load_appsec(reconfigure_tracer=False)
    except Exception:
        # On failure, disable AppSec so the reported signal is false.
        from ddtrace.appsec._listeners import _abort_appsec

        _abort_appsec("startup error")
        raise
    finally:
        # Also reported here for AWS Lambda, where start() is called directly.
        _report_agentic_onboarding()


def restart(join=False):
    pass


def stop(join=False):
    pass
