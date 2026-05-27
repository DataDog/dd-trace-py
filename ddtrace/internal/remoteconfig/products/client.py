from ddtrace import config
from ddtrace.internal.remoteconfig.client import config as rc_config
from ddtrace.internal.settings._agent import config as agent_config


# Flare state - managed globally
_flare_state = None


# TODO: Modularize better into their own respective components
def _register_rc_products() -> None:
    """Enable fetching configuration from Datadog."""
    global _flare_state

    from ddtrace.internal.flare._subscribers import TracerFlareCallback
    from ddtrace.internal.flare._subscribers import TracerFlareState
    from ddtrace.internal.flare.flare import Flare
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    flare = Flare(
        trace_agent_url=str(agent_config.trace_agent_url), api_key=config._dd_api_key, ddconfig=config.__dict__
    )

    # Create shared state
    _flare_state = TracerFlareState()

    # Create the callback (stale check logic is now handled inside the callback)
    flare_callback = TracerFlareCallback(flare, _flare_state)

    # Register for both AGENT_CONFIG and AGENT_TASK products (they share the same callback)
    remoteconfig_poller.register_callback("AGENT_CONFIG", flare_callback)
    remoteconfig_poller.enable_product("AGENT_CONFIG", start_poller=False)
    remoteconfig_poller.register_callback("AGENT_TASK", flare_callback)
    remoteconfig_poller.enable_product("AGENT_TASK", start_poller=False)

    from ddtrace.internal.settings.openfeature import config as ffe_config

    if ffe_config.experimental_flagging_provider_enabled:
        from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc

        enable_featureflags_rc(start_poller=False)


def post_preload():
    pass


def enabled():
    return config._remote_config_enabled


def start():
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    _register_rc_products()
    remoteconfig_poller.enable()


def restart(join=False):
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.reset_at_fork()


def stop(join=False):
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.disable(join=join)


def skip_exit():
    return rc_config.skip_shutdown
