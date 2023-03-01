import time
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Optional
from typing import Type

from ddtrace import config as tracer_config
from ddtrace.debugging._config import config
from ddtrace.debugging._expressions import dd_compile
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import DDExpression
from ddtrace.debugging._probe.model import DEFAULT_PROBE_CONDITION_ERROR_RATE
from ddtrace.debugging._probe.model import DEFAULT_PROBE_RATE
from ddtrace.debugging._probe.model import DEFAULT_SNAPSHOT_PROBE_RATE
from ddtrace.debugging._probe.model import ExpressionTemplateSegment
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricLineProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.model import ProbeType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.client import ConfigMetadata
from ddtrace.internal.remoteconfig.client import RemoteConfigCallBack
from ddtrace.internal.utils.cache import LFUCache


log = get_logger(__name__)


_EXPRESSION_CACHE = LFUCache()


def xlate_keys(d, mapping):
    # type: (Dict[str, Any], Dict[str, str]) -> Dict[str, Any]
    return {mapping.get(k, k): v for k, v in d.items()}


def _invalid_expression(_):
    """Forces probes with invalid expression/conditions to never trigger.

    Any signs of invalid conditions in logs is an indication of a problem with
    the expression compiler.
    """
    return None


INVALID_EXPRESSION = _invalid_expression


def _compile_expression(expr):
    # type: (Optional[Dict[str, Any]]) -> Optional[DDExpression]
    global _EXPRESSION_CACHE, INVALID_EXPRESSION

    if expr is None:
        return None

    ast = expr["json"]

    def compile_or_invalid(expr):
        # type: (str) -> Callable[[Dict[str, Any]], Any]
        try:
            return dd_compile(ast)
        except Exception:
            log.error("Cannot compile expression: %s", expr, exc_info=True)
            return INVALID_EXPRESSION

    dsl = expr["dsl"]

    compiled = _EXPRESSION_CACHE.get(dsl, compile_or_invalid)  # type: Callable[[Dict[str, Any]], Any]

    if compiled is INVALID_EXPRESSION:
        log.error("Cannot compile expression: %s", dsl, exc_info=True)

    return DDExpression(dsl=dsl, callable=compiled)


def _compile_segment(segment):
    if segment.get("str", ""):
        return LiteralTemplateSegment(str_value=segment["str"])
    elif segment.get("json", None) is not None:
        return ExpressionTemplateSegment(expr=_compile_expression(segment))

    # what type of error we should show here?
    return None


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


def _create_probe_based_on_location(args, attribs, line_class, function_class):
    # type: (Dict[str, Any], Dict[str, Any], Type, Type) -> Any
    if attribs["where"].get("sourceFile", None):
        ProbeType = line_class
        args["source_file"] = attribs["where"]["sourceFile"]
        args["line"] = int(attribs["where"]["lines"][0])
    else:
        ProbeType = function_class
        args["module"] = attribs["where"].get("type") or attribs["where"]["typeName"]
        args["func_qname"] = attribs["where"].get("method") or attribs["where"]["methodName"]
        args["evaluate_at"] = attribs.get("evaluateAt")

    return ProbeType(**args)


def probe_factory(attribs):
    # type: (Dict[str, Any]) -> Probe
    """
    Create a new Probe instance.
    """
    try:
        _type = attribs["type"]
        _id = attribs["id"]
    except KeyError as e:
        raise ValueError("Invalid probe attributes: %s" % e)

    if _type == ProbeType.LOG_PROBE:
        take_snapshot = attribs.get("captureSnapshot", False)

        rate = DEFAULT_SNAPSHOT_PROBE_RATE if take_snapshot else DEFAULT_PROBE_RATE
        sampling = attribs.get("sampling")
        if sampling is not None:
            rate = sampling.get("snapshotsPerSecond", rate)

        args = dict(
            probe_id=_id,
            condition=_compile_expression(attribs.get("when")),
            tags=dict(_.split(":", 1) for _ in attribs.get("tags", [])),
            rate=rate,
            limits=CaptureLimits(
                **xlate_keys(
                    attribs["capture"],
                    {
                        "maxReferenceDepth": "max_level",
                        "maxCollectionSize": "max_size",
                        "maxLength": "max_len",
                        "maxFieldDepth": "max_fields",
                    },
                )
            )
            if "capture" in attribs
            else None,
            condition_error_rate=DEFAULT_PROBE_CONDITION_ERROR_RATE,  # TODO: should we take rate limit out of Probe?
            take_snapshot=take_snapshot,
            template=attribs.get("template"),
            segments=[_compile_segment(segment) for segment in attribs.get("segments", [])],
        )

        return _create_probe_based_on_location(args, attribs, LogLineProbe, LogFunctionProbe)

    elif _type == ProbeType.METRIC_PROBE:
        args = dict(
            probe_id=_id,
            condition=_compile_expression(attribs.get("when")),
            tags=dict(_.split(":", 1) for _ in attribs.get("tags", [])),
            name=attribs["metricName"],
            kind=attribs["kind"],
            rate=DEFAULT_PROBE_RATE,  # unused
            condition_error_rate=DEFAULT_PROBE_CONDITION_ERROR_RATE,  # TODO: should we take rate limit out of Probe?
            value=_compile_expression(attribs.get("value")),
        )

        return _create_probe_based_on_location(args, attribs, MetricLineProbe, MetricFunctionProbe)

    raise ValueError("Unknown probe type: %s" % _type)


@_filter_by_env_and_version
def get_probes(config_id, config):
    # type: (str, dict) -> Iterable[Probe]
    return [probe_factory(config)]


log = get_logger(__name__)


class ProbePollerEvent(object):
    NEW_PROBES = 0
    DELETED_PROBES = 1
    MODIFIED_PROBES = 2
    STATUS_UPDATE = 3


ProbePollerEventType = int


class ProbeRCAdapter(RemoteConfigCallBack):
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
        modified_probes = []  # DEV: Probes are currently immutable

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
