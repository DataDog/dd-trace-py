from itertools import chain
import re
import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Optional

from ddtrace import config as tracer_config
from ddtrace.debugging._config import config
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import MetricProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.utils.cache import LFUCache


log = get_logger(__name__)


_EXPRESSION_CACHE = LFUCache()


def _invalid_expression(_):
    """Forces probes with invalid expression/conditions to never trigger.

    Any signs of invalid conditions in logs is an indication of a problem with
    the expression compiler.
    """
    return False


INVALID_EXPRESSION = _invalid_expression


def _compile_expression(when):
    # type: (Optional[Dict[str, Any]]) -> Optional[Callable[[Dict[str, Any]], Any]]
    global _EXPRESSION_CACHE, INVALID_EXPRESSION

    if when is None:
        return None

    ast = when["json"]

    def compile_or_invalid(expr):
        # type: (str) -> Callable[[Dict[str, Any]], Any]
        try:
            return dd_compile(ast)
        except Exception:
            log.error("Cannot compile expression: %s", expr, exc_info=True)
            return INVALID_EXPRESSION

    expr = when["dsl"]

    compiled = _EXPRESSION_CACHE.get(expr, compile_or_invalid)  # type: Callable[[Dict[str, Any]], Any]

    if compiled is INVALID_EXPRESSION:
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
    # type: (Callable[..., Iterable[Probe]]) -> Callable[..., Iterable[Probe]]
    def _wrapper(*args, **kwargs):
        # type: (Any, Any) -> Iterable[Probe]
        return [_ for _ in f(*args, **kwargs) if _match_env_and_version(_)]

    return _wrapper


def probe(_id, _type, attribs):
    # type: (str, str, Dict[str, Any]) -> Probe
    """
    Create a new Probe instance.
    """
    if _type == "snapshotProbes":
        args = dict(
            probe_id=_id,
            condition=_compile_expression(attribs.get("when")),
            active=attribs["active"],
            tags=dict(_.split(":", 1) for _ in attribs.get("tags", [])),
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
            tags=dict(_.split(":", 1) for _ in attribs.get("tags", [])),
            source_file=attribs["where"]["sourceFile"],
            line=int(attribs["where"]["lines"][0]),
            name=attribs["metricName"],
            kind=attribs["kind"],
            value=_compile_expression(attribs.get("value")),
        )

    raise ValueError("Unknown probe type: %s" % _type)


_SNAPSHOT_PREFIX = "snapshotProbe_"
_METRIC_PREFIX = "metricProbe_"
_CONFIG_REGEX = re.compile(r"^[\da-f]{8}-([\da-f]{4}-){3}[\da-f]{12}$", re.IGNORECASE)


def _make_probes(probes, _type):
    return [probe(p["id"], _type, p) for p in probes]


@_filter_by_env_and_version
def get_probes(config_id, config):
    # type: (str, dict) -> Iterable[Probe]

    if config_id.startswith(_SNAPSHOT_PREFIX):
        return _make_probes([config], "snapshotProbes")

    if config_id.startswith(_METRIC_PREFIX):
        return chain(_make_probes([config], "metricProbes"))

    if _CONFIG_REGEX.match(config_id):
        return chain(
            _make_probes(config.get("snapshotProbes") or [], "snapshotProbes"),
            _make_probes(config.get("metricProbes") or [], "metricProbes"),
        )

    raise ValueError("Unsupported config id: %s" % config_id)


log = get_logger(__name__)


class ProbePollerEvent(object):
    NEW_PROBES = 0
    DELETED_PROBES = 1
    MODIFIED_PROBES = 2
    STATUS_UPDATE = 3


ProbePollerEventType = int


def probe_modified(reference, probe):
    # type: (Probe, Probe) -> bool
    # DEV: Probes are immutable modulo the active state. Hence we expect
    # probes with the same ID to differ up to this property for now.
    return probe.active != reference.active


class ProbeRCAdapter(object):
    """Probe configuration adapter for the RCM client.

    This adapter turns configuration events from the RCM client into probe
    events that can be handled easily by the debugger.
    """

    def __init__(self, callback):
        # type: (Callable[[ProbePollerEventType, Iterable[Probe]], None]) -> None
        self._callback = callback
        self._configs = {}  # type: Dict[str, Dict[str, Probe]]
        self._next_status_update_timestamp()

    def _next_status_update_timestamp(self):
        # type: () -> None
        self._status_timestamp = time.time() + config.diagnostics_interval

    def _dispatch_probe_events(self, prev_probes, next_probes):
        new_probes = [p for _, p in next_probes.items() if _ not in prev_probes]
        deleted_probes = [p for _, p in prev_probes.items() if _ not in next_probes]
        modified_probes = [p for _, p in next_probes.items() if _ in prev_probes and probe_modified(p, prev_probes[_])]

        if deleted_probes:
            self._callback(ProbePollerEvent.DELETED_PROBES, deleted_probes)
        if modified_probes:
            self._callback(ProbePollerEvent.MODIFIED_PROBES, modified_probes)
        if new_probes:
            self._callback(ProbePollerEvent.NEW_PROBES, new_probes)

    def _update_probes_for_config(self, config_id, config):
        # type: (str, Any) -> None
        prev_probes = self._configs.get(config_id, {})  # type: Dict[str, Probe]
        next_probes = (
            {probe.probe_id: probe for probe in get_probes(config_id, config)} if config is not None else {}
        )  # type: Dict[str, Probe]

        self._dispatch_probe_events(prev_probes, next_probes)

        if next_probes:
            self._configs[config_id] = next_probes
        else:
            self._configs.pop(config_id, None)

    def __call__(self, metadata, config):
        # type: (Optional[ConfigMetadata], Any) -> None

        # DEV: We emit a status update event here to avoid having to spawn a
        # separate thread for this.
        if time.time() > self._status_timestamp:
            log.debug("Emitting probe status log messages")
            probes = [probe for config in self._configs.values() for probe in config.values()]
            self._callback(ProbePollerEvent.STATUS_UPDATE, probes)
            self._next_status_update_timestamp()

        if metadata is None:
            log.warning("no metadata was provided")
            return

        self._update_probes_for_config(metadata.id, config)
