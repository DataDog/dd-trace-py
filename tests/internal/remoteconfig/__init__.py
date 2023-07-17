from contextlib import contextmanager

import httpretty

from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from tests.utils import override_env


@contextmanager
def rcm_endpoint(port=10126, poll_interval=0.05):
    with override_env(
        dict(
            DD_TRACE_AGENT_URL="http://localhost:%d" % port,
            DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS=str(poll_interval),
        )
    ), httpretty.enabled():
        httpretty.register_uri(
            httpretty.GET, "http://localhost:%d/info" % port, body='{"endpoints":["%s"]}' % REMOTE_CONFIG_AGENT_ENDPOINT
        )
        yield
