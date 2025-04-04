import typing

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants
import ddtrace.appsec._ddwaf as ddwaf
from ddtrace.appsec._deduplications import deduplication
from ddtrace.internal import telemetry
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


DDWAF_VERSION = ddwaf.version()
UNKNOWN_VERSION = "unknown"

bool_str = ("false", "true")

logger = ddlogger.get_logger(__name__)


class WARNING_TAGS(metaclass=_constants.Constant_Class):
    TELEMETRY_LOGS = "telemetry_logs"
    TELEMETRY_METRICS = "telemetry_metrics"


# limit warnings to one per day per process
for _, tag in WARNING_TAGS:
    ddlogger.set_tag_rate_limit(tag, ddlogger.HOUR)

log_extra = {"product": "appsec", "stack_limit": 4, "exec_limit": 4}


@deduplication
def _set_waf_error_log(msg: str, version: str, error_level: bool = True) -> None:
    """used for waf configuration errors"""
    try:
        log_tags = {
            "waf_version": DDWAF_VERSION,
            "event_rules_version": version or UNKNOWN_VERSION,
            "lib_language": "python",
        }
        level = TELEMETRY_LOG_LEVEL.ERROR if error_level else TELEMETRY_LOG_LEVEL.WARNING
        telemetry.telemetry_writer.add_log(level, msg, tags=log_tags)
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":waf:error"}
        logger.warning(WARNING_TAGS.TELEMETRY_LOGS, extra=extra, exc_info=True)
    try:
        tags = (
            ("waf_version", DDWAF_VERSION),
            ("event_rules_version", version or UNKNOWN_VERSION),
        )
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "waf.config_errors", 1, tags=tags)
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":waf:config_errors"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)


def _set_waf_updates_metric(info, success: bool):
    try:
        if info:
            tags: typing.Tuple[typing.Tuple[str, str], ...] = (
                ("event_rules_version", info.version or UNKNOWN_VERSION),
                ("waf_version", DDWAF_VERSION),
                ("success", bool_str[success]),
            )
        else:
            tags = (("waf_version", DDWAF_VERSION), ("success", bool_str[success]))

        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "waf.updates", 1, tags=tags)
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":waf:updates"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)


def _set_waf_init_metric(info, success: bool):
    try:
        if info:
            tags: typing.Tuple[typing.Tuple[str, str], ...] = (
                ("event_rules_version", info.version or UNKNOWN_VERSION),
                ("waf_version", DDWAF_VERSION),
                ("success", bool_str[success]),
            )
        else:
            tags = (("waf_version", DDWAF_VERSION), ("success", bool_str[success]))

        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "waf.init", 1, tags=tags)
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":waf:init"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)


_TYPES_AND_TAGS = {
    _constants.EXPLOIT_PREVENTION.TYPE.CMDI: (("rule_type", "command_injection"), ("rule_variant", "exec")),
    _constants.EXPLOIT_PREVENTION.TYPE.SHI: (("rule_type", "command_injection"), ("rule_variant", "shell")),
    _constants.EXPLOIT_PREVENTION.TYPE.LFI: (("rule_type", "lfi"),),
    _constants.EXPLOIT_PREVENTION.TYPE.SSRF: (("rule_type", "ssrf"),),
    _constants.EXPLOIT_PREVENTION.TYPE.SQLI: (("rule_type", "sql_injection"),),
}

TAGS_STRING_LENGTH = (("truncation_reason", "1"),)
TAGS_CONTAINER_SIZE = (("truncation_reason", "2"),)
TAGS_CONTAINER_DEPTH = (("truncation_reason", "4"),)


def _report_waf_truncations(observator):
    try:
        bitfield = 0
        if observator.string_length is not None:
            bitfield |= 1
            telemetry.telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.APPSEC, "waf.truncated_value_size", observator.string_length, TAGS_STRING_LENGTH
            )
        if observator.container_size is not None:
            bitfield |= 2
            telemetry.telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.APPSEC, "waf.truncated_value_size", observator.container_size, TAGS_CONTAINER_SIZE
            )
        if observator.container_depth is not None:
            bitfield |= 4
            telemetry.telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.APPSEC, "waf.truncated_value_size", observator.container_depth, TAGS_CONTAINER_DEPTH
            )
        if bitfield:
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC,
                "waf.input_truncated",
                1,
                tags=(("truncation_reason", str(bitfield)),),
            )
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":waf:truncations"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)


def _report_waf_run_error(error: int, rule_version: str, rule_type: typing.Optional[str]):
    """used for waf run errors"""
    try:
        if rule_type is None:
            waf_tags = (
                ("waf_version", DDWAF_VERSION),
                ("event_rules_version", rule_version or UNKNOWN_VERSION),
                ("waf_error", str(error)),
            )
            telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "waf.error", 1, tags=waf_tags)
        else:
            rasp_tags = (
                ("waf_version", DDWAF_VERSION),
                ("event_rules_version", rule_version or UNKNOWN_VERSION),
                ("waf_error", str(error)),
            ) + _TYPES_AND_TAGS.get(rule_type, ())
            telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "rasp.error", 1, tags=rasp_tags)
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": f":waf:run_error:{rule_type or 'srb'}"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)
        raise


def _set_waf_request_metrics(*_args):
    try:
        result = _asm_request_context.get_waf_telemetry_results()
        if result is not None:
            # TODO: enable it when Telemetry intake accepts this tag
            # is_truncation = any((result.truncation for result in list_results))

            truncation = result.truncation
            input_truncated = bool(truncation.string_length or truncation.container_size or truncation.container_depth)
            tags_request = (
                ("event_rules_version", result.version or UNKNOWN_VERSION),
                ("waf_version", DDWAF_VERSION),
                ("rule_triggered", bool_str[result.triggered]),
                ("request_blocked", bool_str[result.blocked]),
                ("waf_timeout", bool_str[bool(result.timeout)]),
                ("input_truncated", bool_str[input_truncated]),
                ("waf_error", str(result.error)),
                ("rate_limited", bool_str[result.rate_limited]),
            )

            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, "waf.requests", 1, tags=tags_request
            )
            rasp = result.rasp
            if rasp.sum_eval:
                for t, n in [("eval", "rasp.rule.eval"), ("match", "rasp.rule.match"), ("timeout", "rasp.timeout")]:
                    for rule_type, value in getattr(rasp, t).items():
                        if value:
                            tags = _TYPES_AND_TAGS.get(rule_type, ()) + (
                                ("waf_version", DDWAF_VERSION),
                                ("event_rules_version", result.version or UNKNOWN_VERSION),
                            )
                            if t == "match":
                                tags += (("block", ["irrelevant", "success"][result.blocked]),)
                            telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, n, value, tags=tags)
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":waf:request"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)


def _report_api_security(route: bool, schemas: int) -> None:
    framework = _asm_request_context.get_framework()
    try:
        if route:
            metric_name = "api_security.request.schema" if schemas > 0 else "api_security.request.no_schema"
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, metric_name, 1, tags=(("framework", framework),)
            )
        else:
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, "api_security.missing_route", 1, tags=(("framework", framework),)
            )
    except Exception:
        extra = {"product": "appsec", "exec_limit": 6, "more_info": ":api_security"}
        logger.warning(WARNING_TAGS.TELEMETRY_METRICS, extra=extra, exc_info=True)
