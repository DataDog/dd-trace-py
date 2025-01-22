from ddtrace import config
from ddtrace.settings.asm import config as asm_config


def post_preload():
    pass


def start():
    if asm_config._asm_enabled or config._remote_config_enabled:
        from ddtrace.appsec._remoteconfiguration import enable_appsec_rc

        enable_appsec_rc()


def restart(join=False):
    if asm_config._asm_enabled or config._remote_config_enabled:
        from ddtrace.appsec._remoteconfiguration import APPSEC_PRODUCTS
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.start_subscribers_by_product(APPSEC_PRODUCTS)


def stop(join=False):
    from ddtrace.appsec._remoteconfiguration import disable_appsec_rc

    disable_appsec_rc()


def at_exit(join=False):
    pass
