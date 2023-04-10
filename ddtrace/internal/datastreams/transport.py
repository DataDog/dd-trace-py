import ddtrace

from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..writer import _human_size

AGENT_URL = "http://{}:{}".format("localhost", 8126)
ENDPOINT = "/v0.1/pipeline_stats"

log = get_logger(__name__)


def flush_stats(payload):
    # type: (bytes) -> None
    headers = {
        "Datadog-Meta-Lang": "python",
        "Datadog-Meta-Tracer-Version": ddtrace.__version__,
        "Content-Type": "application/msgpack",
    }  # type: Dict[str, str]
    try:
        conn = get_connection(AGENT_URL)
        conn.request("PUT", ENDPOINT, payload, headers)
        resp = get_connection_response(conn)
    except Exception:
        log.error("failed to submit span stats to the Datadog agent at %s", ENDPOINT, exc_info=True)
        raise
    else:
        if resp.status == 404:
            log.error(
                "Datadog agent does not support data streams monitoring, disabling, please upgrade your agent"
            )
            # self._enabled = False
            return
        elif resp.status >= 400:
            log.error(
                "failed to send stats payload, %s (%s) (%s) response from Datadog agent at %s",
                resp.status,
                resp.reason,
                resp.read(),
                ENDPOINT,
            )
        else:
            log.info("sent %s to %s", _human_size(len(payload)), ENDPOINT)
