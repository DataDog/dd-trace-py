from ddtrace import config
from ddtrace.internal.remoteconfig.client import config as rc_config


# TODO: Modularize better into their own respective components
def _register_rc_products() -> None:
    """Enable fetching configuration from Datadog."""
    from ddtrace.internal.flare.flare import Flare
    from ddtrace.internal.flare.handler import _handle_tracer_flare
    from ddtrace.internal.flare.handler import _tracerFlarePubSub
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    flare = Flare(trace_agent_url=config._trace_agent_url, api_key=config._dd_api_key, ddconfig=config.__dict__)
    tracerflare_pubsub = _tracerFlarePubSub()(_handle_tracer_flare, flare)
    remoteconfig_poller.register("AGENT_CONFIG", tracerflare_pubsub)
    remoteconfig_poller.register("AGENT_TASK", tracerflare_pubsub)


def post_preload():
    pass


def start():
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.enable()
        _register_rc_products()


def restart(join=False):
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.reset_at_fork()


def stop(join=False):
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.disable(join=join)


def at_exit(join=False):
    if config._remote_config_enabled and not rc_config.skip_shutdown:
        stop(join=join)
