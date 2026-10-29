from typing import TYPE_CHECKING
from typing import Any
from typing import Union

from ddtrace.internal.settings.asm import config


if TYPE_CHECKING:
    from ddtrace.internal.core.subscriber import ContextSubscriber
    from ddtrace.internal.core.subscriber import Subscriber


requires = ["remote-configuration"]

_exploit_prevention_registered = False


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(config._asm_enabled or config._asm_can_be_enabled or config._asm_rc_enabled)


def activated() -> bool:
    # Eligibility for remote activation starts the lifecycle but does not make AppSec active.
    return bool(config._asm_enabled)


def start() -> None:
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


def register_subscribers() -> None:
    # Remote configuration activates AppSec outside of start(), so _enable_asm() calls this too.
    global _exploit_prevention_registered
    if _exploit_prevention_registered or not (config._asm_enabled and config._ep_enabled):
        return
    for subscriber in _exploit_prevention_subscribers():
        subscriber.register()
    _exploit_prevention_registered = True


def unregister_subscribers() -> None:
    global _exploit_prevention_registered
    if not _exploit_prevention_registered:
        return
    for subscriber in _exploit_prevention_subscribers():
        subscriber.unregister()
    _exploit_prevention_registered = False


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass


def _exploit_prevention_subscribers() -> tuple[Union[type["Subscriber"], type["ContextSubscriber[Any]"]], ...]:
    from ddtrace.appsec._contrib.dbapi.subscribers import AppSecDbApiSubscriber
    from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxRequestContextSubscriber
    from ddtrace.appsec._contrib.httpx.subscribers import AppSecHttpxSingleRequestContextSubscriber
    from ddtrace.appsec._contrib.subprocess.subscribers import AppSecSubprocessCommandSubscriber

    return (
        AppSecDbApiSubscriber,
        AppSecHttpxRequestContextSubscriber,
        AppSecHttpxSingleRequestContextSubscriber,
        AppSecSubprocessCommandSubscriber,
    )


def _disable_asm() -> None:
    if config._asm_enabled:
        from ddtrace.appsec._listeners import disable_appsec
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        disable_appsec(reconfigure_tracer=True)
        unregister_subscribers()
        if not config._asm_enabled:
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)


def _enable_asm() -> None:
    if config._asm_can_be_enabled and not config._asm_enabled:
        from ddtrace.appsec._constants import APPSEC
        from ddtrace.appsec._listeners import load_appsec
        from ddtrace.internal.telemetry import telemetry_writer
        from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT

        if load_appsec(reconfigure_tracer=True, origin=APPSEC.ENABLED_ORIGIN_RC):
            register_subscribers()
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
