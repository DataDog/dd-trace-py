"""Shared transport for /pipeline_stats payloads (classic DSM and the pipelines reporter).

Both flushers POST a gzip'd msgpack payload to the agent and interpret the
response the same way: 404 means the agent predates pipeline stats support,
>= 400 is a rejected payload, anything else is success. Connection failures
raise :class:`TransportError`; the caller decides whether to propagate (classic
DSM retries with backoff) or swallow (the pipelines flusher never dies).
"""

from __future__ import annotations

from typing import Dict

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import _human_size

from ..agent import get_connection


log = get_logger(__name__)


class TransportError(Exception):
    """The POST to the agent failed; the underlying error is the cause."""


def post(
    agent_url: str,
    endpoint: str,
    payload: bytes,
    headers: Dict[str, str],
    timeout: float,
    what: str,
    unsupported_feature: str,
    ok_noun: str = "",
) -> None:
    """POST ``payload`` to ``agent_url + endpoint`` and interpret the response.

    ``what``/``unsupported_feature``/``ok_noun`` only name the payload in the
    log messages so each caller keeps its exact historical wording.
    """
    try:
        conn = get_connection(agent_url, timeout)
        conn.request("POST", endpoint, payload, headers)
        resp = conn.getresponse()
    except Exception as e:
        raise TransportError("failed to POST %s to the Datadog agent" % endpoint) from e

    agent_endpoint = "%s%s" % (agent_url, endpoint)
    if resp.status == 404:
        log.error("Datadog agent does not support %s. Upgrade to 7.34+", unsupported_feature)
        return
    if resp.status >= 400:
        log.error(
            "failed to send %s payload, %s (%s) (%s) response from Datadog agent at %s",
            what,
            resp.status,
            resp.reason,
            resp.read(),
            agent_endpoint,
        )
        return
    log.debug("sent %s %s to %s", _human_size(len(payload)), ok_noun, agent_endpoint)
