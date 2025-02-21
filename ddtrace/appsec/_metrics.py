from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants
import ddtrace.appsec._ddwaf as ddwaf
from ddtrace.appsec._deduplications import deduplication
from ddtrace.internal import telemetry
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)

DDWAF_VERSION = ddwaf.version()


@deduplication
def _set_waf_error_log(msg: str, version: str, error_level: bool = True) -> None:
    tags = {
        "waf_version": DDWAF_VERSION,
        "lib_language": "python",
    }
    if version:
        tags["event_rules_version"] = version
    level = TELEMETRY_LOG_LEVEL.ERROR if error_level else TELEMETRY_LOG_LEVEL.WARNING
    telemetry.telemetry_writer.add_log(level, msg, tags=tags)


def _set_waf_updates_metric(info):
    try:
        if info and info.version:
            tags = (
                ("event_rules_version", info.version),
                ("waf_version", DDWAF_VERSION),
            )
        else:
            tags = (("waf_version", DDWAF_VERSION),)

        telemetry.telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.APPSEC,
            "waf.updates",
            1.0,
            tags=tags,
        )
    except Exception:
        log.warning("Error reporting ASM WAF updates metrics", exc_info=True)


def _set_waf_init_metric(info):
    try:
        if info and info.version:
            tags = (
                ("event_rules_version", info.version),
                ("waf_version", DDWAF_VERSION),
            )
        else:
            tags = (("waf_version", DDWAF_VERSION),)

        telemetry.telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.APPSEC,
            "waf.init",
            1.0,
            tags=tags,
        )
    except Exception:
        log.warning("Error reporting ASM WAF init metrics", exc_info=True)


_TYPES_AND_TAGS = {
    _constants.EXPLOIT_PREVENTION.TYPE.CMDI: (("rule_type", "command_injection"), ("rule_variant", "exec")),
    _constants.EXPLOIT_PREVENTION.TYPE.SHI: (("rule_type", "command_injection"), ("rule_variant", "shell")),
    _constants.EXPLOIT_PREVENTION.TYPE.LFI: (("rule_type", "lfi"),),
    _constants.EXPLOIT_PREVENTION.TYPE.SSRF: (("rule_type", "ssrf"),),
    _constants.EXPLOIT_PREVENTION.TYPE.SQLI: (("rule_type", "sql_injection"),),
}


def _set_waf_request_metrics(*args):
    try:
        result = _asm_request_context.get_waf_telemetry_results()
        if result is not None and result["version"] is not None:
            # TODO: enable it when Telemetry intake accepts this tag
            # is_truncation = any((result.truncation for result in list_results))

            tags_request = (
                ("event_rules_version", result["version"]),
                ("waf_version", DDWAF_VERSION),
                ("rule_triggered", str(result["triggered"]).lower()),
                ("request_blocked", str(result["blocked"]).lower()),
                ("waf_timeout", str(result["timeout"]).lower()),
            )

            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC,
                "waf.requests",
                1.0,
                tags=tags_request,
            )
            rasp = result["rasp"]
            if rasp["sum_eval"]:
                for t, n in [("eval", "rasp.rule.eval"), ("match", "rasp.rule.match"), ("timeout", "rasp.timeout")]:
                    for rule_type, value in rasp[t].items():
                        if value:
                            telemetry.telemetry_writer.add_count_metric(
                                TELEMETRY_NAMESPACE.APPSEC,
                                n,
                                float(value),
                                tags=_TYPES_AND_TAGS.get(rule_type, ()) + (("waf_version", DDWAF_VERSION),),
                            )

    except Exception:
        log.warning("Error reporting ASM WAF requests metrics", exc_info=True)
    finally:
        if result is not None:
            result["triggered"] = False
            result["blocked"] = False
            result["timeout"] = False
            result["version"] = None
