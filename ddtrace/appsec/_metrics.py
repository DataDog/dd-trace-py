from ddtrace.appsec import _asm_request_context
from ddtrace.appsec.ddwaf import DDWaf_info
from ddtrace.appsec.ddwaf import version
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_metrics_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC


log = get_logger(__name__)


def _set_waf_error_metric(msg, stack_trace, info):
    # type: (str, str, DDWaf_info) -> None
    try:
        tags = {
            "waf_version": version(),
            "lib_language": "python",
        }
        if info and info.version:
            tags["event_rules_version"] = info.version
        telemetry_metrics_writer.add_log("ERROR", msg, stack_trace=stack_trace, tags=tags)
    except Exception:
        log.warning("Error reporting ASM WAF logs metrics", exc_info=True)


def _set_waf_updates_metric(info):
    try:
        tags = {
            "waf_version": version(),
            "lib_language": "python",
        }
        if info and info.version:
            tags["event_rules_version"] = info.version

        telemetry_metrics_writer.add_count_metric(
            TELEMETRY_NAMESPACE_TAG_APPSEC,
            "waf.updates",
            1.0,
            tags=tags,
        )
    except Exception:
        log.warning("Error reporting ASM WAF updates metrics", exc_info=True)


def _set_waf_init_metric(info):
    try:
        tags = {
            "waf_version": version(),
            "lib_language": "python",
        }
        if info and info.version:
            tags["event_rules_version"] = info.version

        telemetry_metrics_writer.add_count_metric(
            TELEMETRY_NAMESPACE_TAG_APPSEC,
            "waf.init",
            1.0,
            tags=tags,
        )
    except Exception:
        log.warning("Error reporting ASM WAF init metrics", exc_info=True)


def _set_waf_request_metrics():
    try:
        list_results, list_result_info, list_is_blocked = _asm_request_context.get_waf_results()
        if any((list_results, list_result_info, list_is_blocked)):
            is_blocked = any(list_is_blocked)
            is_triggered = any((result.data for result in list_results))
            has_info = any(list_result_info)

            common_tags = {
                "waf_version": version(),
                "lib_language": "python",
                "rule_triggered": is_triggered,
                "request_blocked": is_blocked,
            }

            if has_info and list_result_info[0].version:
                common_tags["event_rules_version"] = list_result_info[0].version

            tags_request = {
                "rule_triggered": is_triggered,
                "request_blocked": is_blocked,
            }
            tags_request.update(common_tags)

            telemetry_metrics_writer.add_count_metric(
                TELEMETRY_NAMESPACE_TAG_APPSEC,
                "waf.requests",
                1.0,
                tags=tags_request,
            )
    except Exception:
        log.warning("Error reporting ASM WAF requests metrics", exc_info=True)
    finally:
        _asm_request_context.reset_waf_results()
