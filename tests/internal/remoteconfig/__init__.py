from contextlib import contextmanager
import socket


# httpretty replaces these when it patches the socket module, and does not reliably
# put them back: with ``httpretty.is_enabled()`` already False they can still point at
# ``httpretty.core``, which leaves every later test in the process unable to open a
# real socket. Snapshot the real implementations at import time -- before any test can
# enable httpretty -- so rcm_endpoint can restore them itself.
_REAL_SOCKET_ATTRS = {
    name: getattr(socket, name) for name in ("socket", "create_connection", "getaddrinfo", "socketpair")
}


@contextmanager
def rcm_endpoint(port=10126, poll_interval=0.05):
    """Mock the agent ``/info`` endpoint so the RC poller's agent check passes.

    ``httpretty`` is imported lazily so this package can be imported without it.
    """
    import httpretty

    from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
    from tests.utils import override_env
    from tests.utils import override_global_config

    try:
        with (
            override_env(dict(DD_TRACE_AGENT_URL="http://localhost:%d" % port)),
            httpretty.enabled(),
            override_global_config(dict(_remote_config_poll_interval=poll_interval)),
        ):
            httpretty.register_uri(
                httpretty.GET,
                "http://localhost:%d/info" % port,
                body='{"endpoints":["%s"]}' % REMOTE_CONFIG_AGENT_ENDPOINT,
            )
            yield
    finally:
        for name, real in _REAL_SOCKET_ATTRS.items():
            setattr(socket, name, real)
