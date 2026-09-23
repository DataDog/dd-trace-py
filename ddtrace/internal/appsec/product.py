from ddtrace.internal.settings.asm import config


requires = ["remote-configuration"]


def post_preload():
    pass


def enabled():
    return config._asm_enabled or config._asm_can_be_enabled or config._asm_rc_enabled


def start():
    if config._asm_enabled or config._asm_can_be_enabled:
        # The product owns common-patch setup for both static and remote activation.
        from ddtrace.appsec._listeners import load_common_appsec_modules

        load_common_appsec_modules()

    if config._asm_rc_enabled:
        from ddtrace.appsec._remoteconfiguration import AppSecCallback
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc(AppSecCallback(_enable_asm, _disable_asm))

    if config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        load_appsec(reconfigure_tracer=False)


def restart(join=False):
    pass


def stop(join=False):
    pass


def _disable_asm() -> None:
    if config._asm_enabled:
        from ddtrace.appsec._listeners import disable_appsec
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        disable_appsec(reconfigure_tracer=True)
        if not config._asm_enabled:
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)


def _enable_asm() -> None:
    if config._asm_can_be_enabled and not config._asm_enabled:
        from ddtrace.appsec._constants import APPSEC
        from ddtrace.appsec._listeners import load_appsec
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        if load_appsec(reconfigure_tracer=True, origin=APPSEC.ENABLED_ORIGIN_RC):
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
