from ddtrace.appsec import _asm_request_context
from ddtrace.appsec.ddwaf import version
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_metrics_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC


log = get_logger(__name__)


def _set_waf_updates_metric(event_rules_version):
    try:
        tags = {
            "waf_version": version(),
            "lib_language": "python",
        }
        if event_rules_version:
            tags["event_rules_version"] = event_rules_version

        telemetry_metrics_writer.add_count_metric(
            TELEMETRY_NAMESPACE_TAG_APPSEC,
            "waf.updates",
            1.0,
            tags=tags,
        )
    except Exception:
        log.warning("Error reporting ASM WAF updates metrics", exc_info=True)


def _set_waf_init_metric(event_rules_version):
    try:
        tags = {
            "waf_version": version(),
            "lib_language": "python",
        }
        if event_rules_version:
            tags["event_rules_version"] = event_rules_version

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

            tags = {
                "waf_version": version(),
                "lib_language": "python",
                "rule_triggered": is_triggered,
                "request_blocked": is_blocked,
            }

            if has_info and list_result_info[0].version:
                tags["event_rules_version"] = list_result_info[0].version

            if list_results:
                # runtime is the result in microseconds. Update to milliseconds
                ddwaf_result_runtime = sum(float(ddwaf_result.runtime) for ddwaf_result in list_results)
                ddwaf_result_total_runtime = sum(float(ddwaf_result.runtime) for ddwaf_result in list_results)
                telemetry_metrics_writer.add_distribution_metric(
                    TELEMETRY_NAMESPACE_TAG_APPSEC,
                    "waf.duration",
                    float(ddwaf_result_runtime / 1e3),
                    tags=tags,
                )
                telemetry_metrics_writer.add_distribution_metric(
                    TELEMETRY_NAMESPACE_TAG_APPSEC,
                    "waf.duration_ext",
                    float(ddwaf_result_total_runtime / 1e3),
                    tags=tags,
                )

            telemetry_metrics_writer.add_count_metric(
                TELEMETRY_NAMESPACE_TAG_APPSEC,
                "waf.requests",
                1.0,
                tags=tags,
            )
            # TODO: add log metric to report info.failed and info.errors
    except Exception:
        log.warning("Error reporting ASM WAF requests metrics", exc_info=True)
    finally:
        _asm_request_context.reset_waf_results()
