import dataclasses
import errno
import json
from json.decoder import JSONDecodeError
import os
import os.path
import traceback
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union
import weakref

from ddtrace import config
from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._capabilities import _appsec_rc_file_is_not_static
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._constants import WAF_CONTEXT_NAMES
from ddtrace.appsec._constants import WAF_DATA_NAMES
from ddtrace.appsec._ddwaf import DDWaf_result
from ddtrace.appsec._ddwaf.ddwaf_types import ddwaf_context_capsule
from ddtrace.appsec._metrics import _set_waf_error_metric
from ddtrace.appsec._metrics import _set_waf_init_metric
from ddtrace.appsec._metrics import _set_waf_request_metrics
from ddtrace.appsec._metrics import _set_waf_updates_metric
from ddtrace.appsec._trace_utils import _asm_manual_keep
from ddtrace.appsec._utils import has_triggers
from ddtrace.constants import ORIGIN_KEY
from ddtrace.constants import RUNTIME_FAMILY
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def _transform_headers(data: Union[Dict[str, str], List[Tuple[str, str]]]) -> Dict[str, Union[str, List[str]]]:
    normalized: Dict[str, Union[str, List[str]]] = {}
    headers = data if isinstance(data, list) else data.items()
    for header, value in headers:
        header = header.lower()
        if header in ("cookie", "set-cookie"):
            continue
        if header in normalized:  # if a header with the same lowercase name already exists, let's make it an array
            existing = normalized[header]
            if isinstance(existing, list):
                existing.append(value)
            else:
                normalized[header] = [existing, value]
        else:
            normalized[header] = value
    return normalized


def get_rules() -> str:
    return os.getenv("DD_APPSEC_RULES", default=DEFAULT.RULES)


def get_appsec_obfuscation_parameter_key_regexp() -> bytes:
    return os.getenvb(b"DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP", DEFAULT.APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP)


def get_appsec_obfuscation_parameter_value_regexp() -> bytes:
    return os.getenvb(
        b"DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP", DEFAULT.APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP
    )


_COLLECTED_REQUEST_HEADERS_ASM_ENABLED = {
    "accept",
    "content-type",
    "user-agent",
    "x-amzn-trace-id",
    "cloudfront-viewer-ja3-fingerprint",
    "cf-ray",
    "x-cloud-trace-context",
    "x-appgw-trace-id",
    "akamai-user-risk",
    "x-sigsci-requestid",
    "x-sigsci-tags",
}

_COLLECTED_REQUEST_HEADERS = {
    "accept-encoding",
    "accept-language",
    "cf-connecting-ip",
    "cf-connecting-ipv6",
    "content-encoding",
    "content-language",
    "content-length",
    "fastly-client-ip",
    "forwarded",
    "forwarded-for",
    "host",
    "true-client-ip",
    "via",
    "x-client-ip",
    "x-cluster-client-ip",
    "x-forwarded",
    "x-forwarded-for",
    "x-real-ip",
}

_COLLECTED_REQUEST_HEADERS.update(_COLLECTED_REQUEST_HEADERS_ASM_ENABLED)


def _set_headers(span: Span, headers: Any, kind: str, only_asm_enabled: bool = False) -> None:
    from ddtrace.contrib.trace_utils import _normalize_tag_name

    for k in headers:
        if isinstance(k, tuple):
            key, value = k
        else:
            key, value = k, headers[k]
        if key.lower() in (_COLLECTED_REQUEST_HEADERS_ASM_ENABLED if only_asm_enabled else _COLLECTED_REQUEST_HEADERS):
            # since the header value can be a list, use `set_tag()` to ensure it is converted to a string
            span.set_tag(_normalize_tag_name(kind, key), value)


def _get_rate_limiter() -> RateLimiter:
    return RateLimiter(int(os.getenv("DD_APPSEC_TRACE_RATE_LIMIT", DEFAULT.TRACE_RATE_LIMIT)))


@dataclasses.dataclass(eq=False)
class AppSecSpanProcessor(SpanProcessor):
    rules: str = dataclasses.field(default_factory=get_rules)
    obfuscation_parameter_key_regexp: bytes = dataclasses.field(
        default_factory=get_appsec_obfuscation_parameter_key_regexp
    )
    obfuscation_parameter_value_regexp: bytes = dataclasses.field(
        default_factory=get_appsec_obfuscation_parameter_value_regexp
    )
    _addresses_to_keep: Set[str] = dataclasses.field(default_factory=set)
    _rate_limiter: RateLimiter = dataclasses.field(default_factory=_get_rate_limiter)
    _span_to_waf_ctx: weakref.WeakKeyDictionary = dataclasses.field(default_factory=weakref.WeakKeyDictionary)

    @property
    def enabled(self):
        return self._ddwaf is not None

    def __post_init__(self) -> None:
        from ddtrace.appsec._ddwaf import DDWaf

        try:
            with open(self.rules, "r") as f:
                rules = json.load(f)

        except EnvironmentError as err:
            if err.errno == errno.ENOENT:
                log.error("[DDAS-0001-03] ASM could not read the rule file %s. Reason: file does not exist", self.rules)
            else:
                # TODO: try to log reasons
                log.error("[DDAS-0001-03] ASM could not read the rule file %s.", self.rules)
            raise
        except JSONDecodeError:
            log.error("[DDAS-0001-03] ASM could not read the rule file %s. Reason: invalid JSON file", self.rules)
            raise
        except Exception:
            # TODO: try to log reasons
            log.error("[DDAS-0001-03] ASM could not read the rule file %s.", self.rules)
            raise
        try:
            self._ddwaf = DDWaf(rules, self.obfuscation_parameter_key_regexp, self.obfuscation_parameter_value_regexp)
            if not self._ddwaf._handle or self._ddwaf.info.failed:
                stack_trace = "DDWAF.__init__: invalid rules\n ruleset: %s\nloaded:%s\nerrors:%s\n" % (
                    rules,
                    self._ddwaf.info.loaded,
                    self._ddwaf.info.errors,
                )
                _set_waf_error_metric("WAF init error. Invalid rules", stack_trace, self._ddwaf.info)

            _set_waf_init_metric(self._ddwaf.info)
        except ValueError:
            # Partial of DDAS-0005-00
            log.warning("[DDAS-0005-00] WAF initialization failed")
            raise
        self._update_required()

    def _update_required(self):
        self._addresses_to_keep.clear()
        for address in self._ddwaf.required_data:
            self._addresses_to_keep.add(address)
        # we always need the request headers
        self._addresses_to_keep.add(WAF_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES)
        # we always need the response headers
        self._addresses_to_keep.add(WAF_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES)

    def _update_rules(self, new_rules: Dict[str, Any]) -> bool:
        result = False
        if not _appsec_rc_file_is_not_static():
            return result
        try:
            result = self._ddwaf.update_rules(new_rules)
            _set_waf_updates_metric(self._ddwaf.info)
        except TypeError:
            error_msg = "Error updating ASM rules. TypeError exception "
            log.debug(error_msg, exc_info=True)
            _set_waf_error_metric(error_msg, traceback.format_exc(), self._ddwaf.info)
        if not result:
            error_msg = "Error updating ASM rules. Invalid rules"
            log.debug(error_msg)
            _set_waf_error_metric(error_msg, "", self._ddwaf.info)
        self._update_required()
        return result

    def on_span_start(self, span: Span) -> None:
        from ddtrace.contrib import trace_utils

        if span.span_type != SpanTypes.WEB:
            return

        if _asm_request_context.free_context_available():
            _asm_request_context.register(span)
        else:
            new_asm_context = _asm_request_context.asm_request_context_manager()
            new_asm_context.__enter__()
            _asm_request_context.register(span, new_asm_context)

        ctx = self._ddwaf._at_request_start()
        self._span_to_waf_ctx[span] = ctx
        peer_ip = _asm_request_context.get_ip()
        headers = _asm_request_context.get_headers()
        headers_case_sensitive = _asm_request_context.get_headers_case_sensitive()

        span.set_metric(APPSEC.ENABLED, 1.0)
        span.set_tag_str(RUNTIME_FAMILY, "python")

        def waf_callable(custom_data=None, **kwargs):
            return self._waf_action(span._local_root or span, ctx, custom_data, **kwargs)

        _asm_request_context.set_waf_callback(waf_callable)
        if config._telemetry_enabled:
            _asm_request_context.add_context_callback(_set_waf_request_metrics)
        if headers is not None:
            _asm_request_context.set_waf_address(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, headers, span)
            _asm_request_context.set_waf_address(
                SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE, headers_case_sensitive, span
            )
            if not peer_ip:
                return

            ip = trace_utils._get_request_header_client_ip(headers, peer_ip, headers_case_sensitive)
            # Save the IP and headers in the context so the retrieval can be skipped later
            _asm_request_context.set_waf_address(SPAN_DATA_NAMES.REQUEST_HTTP_IP, ip, span)
            if ip and self._is_needed(WAF_DATA_NAMES.REQUEST_HTTP_IP):
                log.debug("[DDAS-001-00] Executing ASM WAF for checking IP block")
                # _asm_request_context.call_callback()
                _asm_request_context.call_waf_callback({"REQUEST_HTTP_IP": None})

    def _waf_action(
        self,
        span: Span,
        ctx: ddwaf_context_capsule,
        custom_data: Optional[Dict[str, Any]] = None,
        crop_trace: Optional[str] = None,
        rule_type: Optional[str] = None,
    ) -> Optional[DDWaf_result]:
        """
        Call the `WAF` with the given parameters. If `custom_data_names` is specified as
        a list of `(WAF_NAME, WAF_STR)` tuples specifying what values of the `WAF_DATA_NAMES`
        constant class will be checked. Else, it will check all the possible values
        from `WAF_DATA_NAMES`.

        If `custom_data_values` is specified, it must be a dictionary where the key is the
        `WAF_DATA_NAMES` key and the value the custom value. If not used, the values will
        be retrieved from the `core`. This can be used when you don't want to store
        the value in the `core` before checking the `WAF`.
        """
        if span.span_type not in (SpanTypes.WEB, SpanTypes.HTTP):
            return None

        if core.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span) or core.get_item(WAF_CONTEXT_NAMES.BLOCKED):
            # We still must run the waf if we need to extract schemas for API SECURITY
            if not custom_data or not custom_data.get("PROCESSOR_SETTINGS", {}).get("extract-schema", False):
                return None

        data = {}
        ephemeral_data = {}
        iter_data = [(key, WAF_DATA_NAMES[key]) for key in custom_data] if custom_data is not None else WAF_DATA_NAMES
        data_already_sent = _asm_request_context.get_data_sent()
        if data_already_sent is None:
            data_already_sent = set()

        # persistent addresses must be sent if api security is used
        force_keys = custom_data.get("PROCESSOR_SETTINGS", {}).get("extract-schema", False) if custom_data else False

        for key, waf_name in iter_data:  # type: ignore[attr-defined]
            if key in data_already_sent:
                continue
            if waf_name not in WAF_DATA_NAMES.PERSISTENT_ADDRESSES:
                value = custom_data.get(key) if custom_data else None
                if value:
                    ephemeral_data[waf_name] = value

            elif self._is_needed(waf_name) or force_keys:
                value = None
                if custom_data is not None and custom_data.get(key) is not None:
                    value = custom_data.get(key)
                elif key in SPAN_DATA_NAMES:
                    value = _asm_request_context.get_value("waf_addresses", SPAN_DATA_NAMES[key])
                # if value is a callable, it's a lazy value for api security that should not be sent now
                if value is not None and not hasattr(value, "__call__"):
                    data[waf_name] = _transform_headers(value) if key.endswith("HEADERS_NO_COOKIES") else value
                    if waf_name in WAF_DATA_NAMES.PERSISTENT_ADDRESSES:
                        data_already_sent.add(key)
                    log.debug("[action] WAF got value %s", SPAN_DATA_NAMES.get(key, key))

        waf_results = self._ddwaf.run(
            ctx, data, ephemeral_data=ephemeral_data or None, timeout_ms=asm_config._waf_timeout
        )

        blocked = {}
        for action, parameters in waf_results.actions.items():
            if action == WAF_ACTIONS.BLOCK_ACTION:
                blocked = parameters
            elif action == WAF_ACTIONS.REDIRECT_ACTION:
                blocked = parameters
                blocked[WAF_ACTIONS.TYPE] = "none"
            elif action == WAF_ACTIONS.STACK_ACTION:
                from ddtrace.appsec._exploit_prevention.stack_traces import report_stack

                stack_trace_id = parameters["stack_id"]
                report_stack("exploit detected", span, crop_trace, stack_id=stack_trace_id)
                for rule in waf_results.data:
                    rule[EXPLOIT_PREVENTION.STACK_TRACE_ID] = stack_trace_id

        if waf_results.data:
            log.debug("[DDAS-011-00] ASM In-App WAF returned: %s. Timeout %s", waf_results.data, waf_results.timeout)

        _asm_request_context.set_waf_telemetry_results(
            self._ddwaf.info.version,
            bool(waf_results.data),
            bool(blocked),
            waf_results.timeout,
            rule_type,
        )
        if blocked:
            core.set_item(WAF_CONTEXT_NAMES.BLOCKED, blocked, span=span)
            core.set_item(WAF_CONTEXT_NAMES.BLOCKED, blocked)

        try:
            info = self._ddwaf.info
            if info.errors:
                errors = json.dumps(info.errors)
                span.set_tag_str(APPSEC.EVENT_RULE_ERRORS, errors)
                log.debug("Error in ASM In-App WAF: %s", errors)
            span.set_tag_str(APPSEC.EVENT_RULE_VERSION, info.version)
            from ddtrace.appsec._ddwaf import version

            span.set_tag_str(APPSEC.WAF_VERSION, version())

            def update_metric(name, value):
                old_value = span.get_metric(name)
                if old_value is None:
                    old_value = 0.0
                span.set_metric(name, value + old_value)

            span.set_metric(APPSEC.EVENT_RULE_LOADED, info.loaded)
            span.set_metric(APPSEC.EVENT_RULE_ERROR_COUNT, info.failed)
            if waf_results.runtime:
                update_metric(APPSEC.WAF_DURATION, waf_results.runtime)
                update_metric(APPSEC.WAF_DURATION_EXT, waf_results.total_runtime)
        except (JSONDecodeError, ValueError):
            log.warning("Error parsing data ASM In-App WAF metrics report %s", info.errors)
        except Exception:
            log.warning("Error executing ASM In-App WAF metrics report: %s", exc_info=True)

        if waf_results.data or blocked:
            # We run the rate limiter only if there is an attack, its goal is to limit the number of collected asm
            # events
            allowed = self._rate_limiter.is_allowed(span.start_ns)
            if not allowed:
                # TODO: add metric collection to keep an eye (when it's name is clarified)
                return waf_results

            for id_tag, kind in [
                (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, "request"),
                (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, "response"),
            ]:
                headers_req = _asm_request_context.get_waf_address(id_tag)
                if headers_req:
                    _set_headers(span, headers_req, kind=kind)

            _asm_request_context.store_waf_results_data(waf_results.data)
            if blocked:
                span.set_tag(APPSEC.BLOCKED, "true")

            # Partial DDAS-011-00
            span.set_tag_str(APPSEC.EVENT, "true")

            remote_ip = _asm_request_context.get_waf_address(SPAN_DATA_NAMES.REQUEST_HTTP_IP)
            if remote_ip:
                # Note that if the ip collection is disabled by the env var
                # DD_TRACE_CLIENT_IP_HEADER_DISABLED actor.ip won't be sent
                span.set_tag_str("actor.ip", remote_ip)

            # Right now, we overwrite any value that could be already there. We need to reconsider when ASM/AppSec's
            # specs are updated.
            _asm_manual_keep(span)
            if span.get_tag(ORIGIN_KEY) is None:
                span.set_tag_str(ORIGIN_KEY, APPSEC.ORIGIN_VALUE)
        return waf_results

    def _is_needed(self, address: str) -> bool:
        return address in self._addresses_to_keep

    def on_span_finish(self, span: Span) -> None:
        try:
            if span.span_type == SpanTypes.WEB:
                # Force to set respond headers at the end
                headers_res = core.get_item(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, span=span)
                if headers_res:
                    _set_headers(span, headers_res, kind="response")

                headers_req = core.get_item(SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, span=span)
                if headers_req:
                    _set_headers(span, headers_req, kind="request", only_asm_enabled=True)

                # this call is only necessary for tests or frameworks that are not using blocking
                if not has_triggers(span) and _asm_request_context.in_context():
                    log.debug("metrics waf call")
                    _asm_request_context.call_waf_callback()

                self._ddwaf._at_request_end()
        finally:
            # release asm context if it was created by the span
            _asm_request_context.unregister(span)

            if span.span_type != SpanTypes.WEB:
                return

            to_delete = []
            for iterspan, ctx in self._span_to_waf_ctx.items():
                # delete all the ddwaf ctxs associated with this span or finished or deleted ones
                if iterspan == span or iterspan.finished:
                    # so we don't change the dictionary size on iteration
                    to_delete.append(iterspan)

            for s in to_delete:
                try:
                    del self._span_to_waf_ctx[s]
                except Exception:  # nosec B110
                    pass
