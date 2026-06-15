from contextlib import contextmanager


@contextmanager
def rcm_endpoint(port=10126, poll_interval=0.05):
    """Mock the agent ``/info`` endpoint so the RC poller's agent check passes.

    ``httpretty`` is imported lazily so this package can be imported without it.
    """
    import httpretty

    from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
    from tests.utils import override_env
    from tests.utils import override_global_config

    with (
        override_env(dict(DD_TRACE_AGENT_URL="http://localhost:%d" % port)),
        httpretty.enabled(),
        override_global_config(dict(_remote_config_poll_interval=poll_interval)),
    ):
        httpretty.register_uri(
            httpretty.GET, "http://localhost:%d/info" % port, body='{"endpoints":["%s"]}' % REMOTE_CONFIG_AGENT_ENDPOINT
        )
        yield
