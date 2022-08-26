import base64
from itertools import chain
import json
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

import attr
import six

from ddtrace import __version__
from ddtrace import config as tracer_config
from ddtrace.debugging._config import config
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import MetricProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.internal import compat
from ddtrace.internal.agent import get_trace_url
from ddtrace.internal.logger import get_logger
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.utils.cache import LFUCache
from ddtrace.internal.utils.http import Connector
from ddtrace.internal.utils.http import connector


log = get_logger(__name__)


_EXPRESSION_CACHE = LFUCache()


def _invalid_condition(_):
    """Forces probes with invalid conditions to never trigger.

    Any signs of invalid conditions in logs is an indication of a problem with
    the expression compiler.
    """
    return False


INVALID_CONDITION = _invalid_condition


def _compile_condition(when):
    # type: (Optional[Dict[str, Any]]) -> Optional[Callable[[Dict[str, Any]], Any]]
    global _EXPRESSION_CACHE, INVALID_CONDITION

    if when is None:
        return None

    ast = when["json"]

    def compile_or_invalid(expr):
        # type: (str) -> Callable[[Dict[str, Any]], Any]
        try:
            return dd_compile(ast)
        except Exception:
            log.error("Cannot compile expression: %s", expr, exc_info=True)
            return INVALID_CONDITION

    expr = when["dsl"]

    compiled = _EXPRESSION_CACHE.get(expr, compile_or_invalid)  # type: Callable[[Dict[str, Any]], Any]

    if compiled is INVALID_CONDITION:
        log.error("Cannot compile expression: %s", expr, exc_info=True)

    return compiled


def _match_env_and_version(probe):
    # type: (Probe) -> bool
    probe_version = probe.tags.get("version", None)
    probe_env = probe.tags.get("env", None)

    return (probe_version is None or probe_version == tracer_config.version) and (
        probe_env is None or probe_env == tracer_config.env
    )


def _filter_by_env_and_version(f):
    # type: (Callable[..., Optional[Iterable[Probe]]]) -> Callable[..., Optional[Iterable[Probe]]]
    def _(*args, **kwargs):
        # type: (Any, Any) -> Optional[List[Probe]]
        probes = f(*args, **kwargs)
        if probes is None:
            return None
        return [_ for _ in probes if _match_env_and_version(_)]

    return _


def probe(_id, _type, attribs):
    # type: (str, str, Dict[str, Any]) -> Probe
    """
    Create a new Probe instance.
    """
    if _type == "snapshotProbes":
        args = dict(
            probe_id=_id,
            condition=_compile_condition(attribs.get("when")),
            active=attribs["active"],
            tags=dict(_.split(":", maxsplit=1) for _ in attribs.get("tags", [])),
        )

        if attribs["where"].get("sourceFile", None):
            ProbeType = LineProbe
            args["source_file"] = attribs["where"]["sourceFile"]
            args["line"] = int(attribs["where"]["lines"][0])
        else:
            ProbeType = FunctionProbe  # type: ignore[assignment]
            args["module"] = attribs["where"].get("type") or attribs["where"]["typeName"]
            args["func_qname"] = attribs["where"].get("method") or attribs["where"]["methodName"]

        return ProbeType(**args)

    elif _type == "metricProbes":
        return MetricProbe(
            probe_id=_id,
            active=attribs["active"],
            tags=dict(_.split(":", maxsplit=1) for _ in attribs.get("tags", [])),
            source_file=attribs["where"]["sourceFile"],
            line=int(attribs["where"]["lines"][0]),
            name=attribs["metricName"],
            kind=attribs["kind"],
        )

    raise ValueError("Unknown probe type: %s" % _type)


@attr.s
class DebuggingRCV07(object):
    service_name = attr.ib(type=str)
    _config_endpoint = attr.ib(type=str, init=False)
    _url = attr.ib(type=str, factory=get_trace_url)
    _connect = attr.ib(type=Connector, init=False)
    _req_payload = attr.ib(type=str, init=False)

    def __attrs_post_init__(self):
        # type: () -> None
        self._config_endpoint = "/v0.7/config"
        self._connect = connector(self._url, timeout=config.config_timeout)
        self._req_payload = json.dumps(
            {
                "client": {
                    "id": "beb0d6a3-75b0-424f-bbb9-98181545f731",
                    "name": "dd-trace-py",
                    "products": ["LIVE_DEBUGGING"],
                    "version": __version__,
                    "state": {},
                    "is_tracer": True,
                    "client_tracer": {
                        "runtime_id": get_runtime_id(),
                        "language": "python",
                        "tracer_version": __version__,
                        "service": self.service_name,
                        "env": tracer_config.env,
                        "app_version": tracer_config.version,
                    },
                },
            }
        )

    @_filter_by_env_and_version
    def get_probes(self):
        # type: () -> Optional[Iterable[Probe]]
        try:
            with self._connect() as conn:
                log.debug("Sending RCM request to the agent: %s", self._req_payload)
                conn.request("POST", self._config_endpoint, self._req_payload)
                resp = compat.get_connection_response(conn)
                if resp.status == 200:
                    try:
                        max_size = config.max_payload_size
                        raw_data = resp.read(max_size + 1)
                        if len(raw_data) > max_size:
                            log.error("Configuration payload size is too large (max: %d)", max_size)
                            return None
                        data = [
                            json.loads(six.ensure_text(base64.b64decode(f["raw"])))
                            for f in json.loads(six.ensure_text(raw_data))["target_files"]
                        ]
                    except KeyError:
                        # Request was OK but the caches are still cold and
                        # returning an empty payload.
                        return []
                    for service_config in data or []:
                        try:
                            if service_config["id"] == self.service_name:
                                break
                        except Exception:
                            log.error("Invalid configuration item received", exc_info=True)
                    else:
                        log.warning("No configurations for service '%s'", self.service_name)
                        return []

                    return chain(
                        [probe(p["id"], "snapshotProbes", p) for p in service_config.get("snapshotProbes") or []],
                        [probe(p["id"], "metricProbes", p) for p in service_config.get("metricProbes") or []],
                    )
                elif resp.status == 204:
                    # No content
                    return []
                elif resp.status == 404:
                    log.warning("Agent has no configuration endpoint '%s'", self._config_endpoint)
                else:
                    log.error("Failed to fetch probe definitions from %s: HTTP %d", self._url, resp.status)

                return None

        except (compat.httplib.HTTPException, OSError, IOError):
            log.error(
                "Failed to get configurations for service '%s' from %s", self.service_name, self._url, exc_info=True
            )
            return None
