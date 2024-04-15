from ddtrace import config
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._ddwaf import DDWaf_info
from ddtrace.appsec._ddwaf import version as _version
from ddtrace.appsec._deduplications import deduplication
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

DDWAF_VERSION = _version()


@deduplication
def _set_waf_error_metric(msg: str, stack_trace: str, info: DDWaf_info) -> None:
    if config._telemetry_enabled:
        # perf - avoid importing telemetry until needed
        from ddtrace.internal import telemetry

        try:
            tags = {
                "waf_version": DDWAF_VERSION,
                "lib_language": "python",
            }
            if info and info.version:
                tags["event_rules_version"] = info.version
            telemetry.telemetry_writer.add_log("ERROR", msg, stack_trace=stack_trace, tags=tags)
        except Exception:
            log.warning("Error reporting ASM WAF logs metrics", exc_info=True)


def _set_waf_updates_metric(info):
    if config._telemetry_enabled:
        # perf - avoid importing telemetry until needed
        from ddtrace.internal import telemetry
        from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC

        try:
            if info and info.version:
                tags = (
                    ("event_rules_version", info.version),
                    ("waf_version", DDWAF_VERSION),
                )
            else:
                tags = (("waf_version", DDWAF_VERSION),)

            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE_TAG_APPSEC,
                "waf.updates",
                1.0,
                tags=tags,
            )
        except Exception:
            log.warning("Error reporting ASM WAF updates metrics", exc_info=True)


def _set_waf_init_metric(info):
    if config._telemetry_enabled:
        # perf - avoid importing telemetry until needed
        from ddtrace.internal import telemetry
        from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC

        try:
            if info and info.version:
                tags = (
                    ("event_rules_version", info.version),
                    ("waf_version", DDWAF_VERSION),
                )
            else:
                tags = (("waf_version", DDWAF_VERSION),)

            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE_TAG_APPSEC,
                "waf.init",
                1.0,
                tags=tags,
            )
        except Exception:
            log.warning("Error reporting ASM WAF init metrics", exc_info=True)


def _set_waf_request_metrics(*args):
    if config._telemetry_enabled:
        # perf - avoid importing telemetry until needed
        from ddtrace.internal import telemetry
        from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_APPSEC

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
                    TELEMETRY_NAMESPACE_TAG_APPSEC,
                    "waf.requests",
                    1.0,
                    tags=tags_request,
                )
        except Exception:
            log.warning("Error reporting ASM WAF requests metrics", exc_info=True)
        finally:
            if result is not None:
                result["triggered"] = False
                result["blocked"] = False
                result["timeout"] = False
                result["version"] = None
