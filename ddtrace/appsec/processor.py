import errno
import json
import os
import os.path
import traceback
from typing import Set
from typing import TYPE_CHECKING

import attr
from six import ensure_binary

from ddtrace import config
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._constants import WAF_CONTEXT_NAMES
from ddtrace.appsec._constants import WAF_DATA_NAMES
from ddtrace.appsec._metrics import _set_waf_error_metric
from ddtrace.appsec._metrics import _set_waf_init_metric
from ddtrace.appsec._metrics import _set_waf_request_metrics
from ddtrace.appsec._metrics import _set_waf_updates_metric
from ddtrace.appsec.ddwaf import DDWaf
from ddtrace.appsec.ddwaf import version
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import ORIGIN_KEY
from ddtrace.constants import RUNTIME_FAMILY
from ddtrace.contrib import trace_utils
from ddtrace.contrib.trace_utils import _normalize_tag_name
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.rate_limiter import RateLimiter


try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Tuple
    from typing import Union

    from ddtrace.appsec.ddwaf.ddwaf_types import ddwaf_context_capsule
    from ddtrace.span import Span


log = get_logger(__name__)


def _transform_headers(data):
    # type: (Union[Dict[str, str], List[Tuple[str, str]]]) -> Dict[str, Union[str, List[str]]]
    normalized = {}  # type: Dict[str, Union[str, List[str]]]
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


def get_rules():
    # type: () -> str
    return os.getenv("DD_APPSEC_RULES", default=DEFAULT.RULES)


def get_appsec_obfuscation_parameter_key_regexp():
    # type: () -> bytes
    return ensure_binary(
        os.getenv("DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP", DEFAULT.APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP)
    )


def get_appsec_obfuscation_parameter_value_regexp():
    # type: () -> bytes
    return ensure_binary(
        os.getenv("DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP", DEFAULT.APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP)
    )


_COLLECTED_REQUEST_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "cf-connecting-ip",
    "cf-connecting-ipv6",
    "content-encoding",
    "content-language",
    "content-length",
    "content-type",
    "fastly-client-ip",
    "forwarded",
    "forwarded-for",
    "host",
    "true-client-ip",
    "user-agent",
    "via",
    "x-client-ip",
    "x-cluster-client-ip",
    "x-forwarded",
    "x-forwarded-for",
    "x-real-ip",
}


def _set_headers(span, headers, kind):
    # type: (Span, Any, str) -> None
    for k in headers:
        if isinstance(k, tuple):
            key, value = k
        else:
            key, value = k, headers[k]
        if key.lower() in _COLLECTED_REQUEST_HEADERS:
            # since the header value can be a list, use `set_tag()` to ensure it is converted to a string
            span.set_tag(_normalize_tag_name(kind, key), value)


def _get_rate_limiter():
    # type: () -> RateLimiter
    return RateLimiter(int(os.getenv("DD_APPSEC_TRACE_RATE_LIMIT", DEFAULT.TRACE_RATE_LIMIT)))


@attr.s(eq=False)
class AppSecSpanProcessor(SpanProcessor):
    rules = attr.ib(type=str, factory=get_rules)
    obfuscation_parameter_key_regexp = attr.ib(type=bytes, factory=get_appsec_obfuscation_parameter_key_regexp)
    obfuscation_parameter_value_regexp = attr.ib(type=bytes, factory=get_appsec_obfuscation_parameter_value_regexp)
    _ddwaf = attr.ib(type=DDWaf, default=None)
    _addresses_to_keep = attr.ib(type=Set[str], factory=set)
    _rate_limiter = attr.ib(type=RateLimiter, factory=_get_rate_limiter)

    @property
    def enabled(self):
        return self._ddwaf is not None

    def __attrs_post_init__(self):
        # type: () -> None
        if self._ddwaf is None:
            try:
                with open(self.rules, "r") as f:
                    rules = json.load(f)
            except EnvironmentError as err:
                if err.errno == errno.ENOENT:
                    log.error(
                        "[DDAS-0001-03] ASM could not read the rule file %s. Reason: file does not exist", self.rules
                    )
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
                self._ddwaf = DDWaf(
                    rules, self.obfuscation_parameter_key_regexp, self.obfuscation_parameter_value_regexp
                )
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
        for address in self._ddwaf.required_data:
            self._mark_needed(address)
        # we always need the request headers
        self._mark_needed(WAF_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES)
        # we always need the response headers
        self._mark_needed(WAF_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES)

    def _update_rules(self, new_rules):
        # type: (Dict[str, Any]) -> bool
        result = False
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
        return result

    def on_span_start(self, span):
        # type: (Span) -> None
        if span.span_type != SpanTypes.WEB:
            return

        if _asm_request_context.free_context_available():
            _asm_request_context.register(span)
        else:
            new_asm_context = _asm_request_context.asm_request_context_manager()
            new_asm_context.__enter__()
            _asm_request_context.register(span, new_asm_context)

        ctx = self._ddwaf._at_request_start()

        peer_ip = _asm_request_context.get_ip()
        headers = _asm_request_context.get_headers()
        headers_case_sensitive = _asm_request_context.get_headers_case_sensitive()

        span.set_metric(APPSEC.ENABLED, 1.0)
        span.set_tag_str(RUNTIME_FAMILY, "python")

        def waf_callable(custom_data=None):
            return self._waf_action(span._local_root or span, ctx, custom_data)

        _asm_request_context.set_waf_callback(waf_callable)
        _asm_request_context.add_context_callback(_set_waf_request_metrics)
        if headers is not None:
            _context.set_items(
                {
                    SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES: headers,
                    SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES_CASE: headers_case_sensitive,
                },
                span=span,
            )
            if not peer_ip:
                return

            ip = trace_utils._get_request_header_client_ip(headers, peer_ip, headers_case_sensitive)
            # Save the IP and headers in the context so the retrieval can be skipped later
            _context.set_item("http.request.remote_ip", ip, span=span)
            if ip and self._is_needed(WAF_DATA_NAMES.REQUEST_HTTP_IP):
                log.debug("[DDAS-001-00] Executing ASM WAF for checking IP block")
                # _asm_request_context.call_callback()
                _asm_request_context.call_waf_callback({"REQUEST_HTTP_IP": None})

    def _waf_action(self, span, ctx, custom_data=None):
        # type: (Span, ddwaf_context_capsule, dict[str, Any] | None) -> None
        """
        Call the `WAF` with the given parameters. If `custom_data_names` is specified as
        a list of `(WAF_NAME, WAF_STR)` tuples specifying what values of the `WAF_DATA_NAMES`
        constant class will be checked. Else, it will check all the possible values
        from `WAF_DATA_NAMES`.

        If `custom_data_values` is specified, it must be a dictionary where the key is the
        `WAF_DATA_NAMES` key and the value the custom value. If not used, the values will
        be retrieved from the `_context`. This can be used when you don't want to store
        the value in the `_context` before checking the `WAF`.
        """

        if span.span_type != SpanTypes.WEB:
            return

        if _context.get_item(WAF_CONTEXT_NAMES.BLOCKED, span=span):
            return

        data = {}
        iter_data = [(key, WAF_DATA_NAMES[key]) for key in custom_data] if custom_data is not None else WAF_DATA_NAMES
        data_already_sent = _asm_request_context.get_data_sent()
        if data_already_sent is None:
            data_already_sent = set()

        # type ignore because mypy seems to not detect that both results of the if
        # above can iter if not None
        for key, waf_name in iter_data:  # type: ignore[attr-defined]
            if self._is_needed(waf_name) and key not in data_already_sent:
                if custom_data is not None and custom_data.get(key) is not None:
                    value = custom_data.get(key)
                else:
                    value = _context.get_item(SPAN_DATA_NAMES[key], span=span)

                if value:
                    data[waf_name] = _transform_headers(value) if key.endswith("HEADERS_NO_COOKIES") else value
                    data_already_sent.add(key)
                    log.debug("[action] WAF got value %s", SPAN_DATA_NAMES[key])

        waf_results = self._ddwaf.run(ctx, data, config._waf_timeout)
        if waf_results and waf_results.data:
            log.debug("[DDAS-011-00] ASM In-App WAF returned: %s. Timeout %s", waf_results.data, waf_results.timeout)

        blocked = WAF_ACTIONS.BLOCK in waf_results.actions
        _asm_request_context.set_waf_results(waf_results, self._ddwaf.info, blocked)
        if blocked:
            _context.set_item(WAF_CONTEXT_NAMES.BLOCKED, True, span=span)

        try:
            info = self._ddwaf.info
            if info.errors:
                errors = json.dumps(info.errors)
                span.set_tag_str(APPSEC.EVENT_RULE_ERRORS, errors)
                _set_waf_error_metric("WAF run. Error", errors, info)
            if waf_results.timeout:
                _set_waf_error_metric("WAF run. Timeout errors", "", info)
            span.set_tag_str(APPSEC.EVENT_RULE_VERSION, info.version)
            span.set_tag_str(APPSEC.WAF_VERSION, version())

            def update_metric(name, value):
                old_value = span.get_metric(name)
                if old_value is None:
                    old_value = 0.0
                span.set_metric(name, value + old_value)

            span.set_metric(APPSEC.EVENT_RULE_LOADED, info.loaded)
            span.set_metric(APPSEC.EVENT_RULE_ERROR_COUNT, info.failed)
            if waf_results:
                update_metric(APPSEC.WAF_DURATION, waf_results.runtime)
                update_metric(APPSEC.WAF_DURATION_EXT, waf_results.total_runtime)
        except (JSONDecodeError, ValueError):
            log.warning("Error parsing data ASM In-App WAF metrics report %s", info.errors)
        except Exception:
            log.warning("Error executing ASM In-App WAF metrics report: %s", exc_info=True)

        if (waf_results and waf_results.data) or blocked:
            # We run the rate limiter only if there is an attack, its goal is to limit the number of collected asm
            # events
            allowed = self._rate_limiter.is_allowed(span.start_ns)
            if not allowed:
                # TODO: add metric collection to keep an eye (when it's name is clarified)
                return

            for id_tag, kind in [
                (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, "request"),
                (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, "response"),
            ]:
                headers_req = _context.get_item(id_tag, span=span)
                if headers_req:
                    _set_headers(span, headers_req, kind=kind)

            if waf_results and waf_results.data:
                span.set_tag_str(APPSEC.JSON, '{"triggers":%s}' % (waf_results.data,))
            if blocked:
                span.set_tag(APPSEC.BLOCKED, "true")
                _set_waf_request_metrics()

            # Partial DDAS-011-00
            span.set_tag_str(APPSEC.EVENT, "true")

            remote_ip = _context.get_item(SPAN_DATA_NAMES.REQUEST_HTTP_IP, span=span)
            if remote_ip:
                # Note that if the ip collection is disabled by the env var
                # DD_TRACE_CLIENT_IP_HEADER_DISABLED actor.ip won't be sent
                span.set_tag_str("actor.ip", remote_ip)

            # Right now, we overwrite any value that could be already there. We need to reconsider when ASM/AppSec's
            # specs are updated.
            span.set_tag(MANUAL_KEEP_KEY)
            if span.get_tag(ORIGIN_KEY) is None:
                span.set_tag_str(ORIGIN_KEY, APPSEC.ORIGIN_VALUE)

    def _mark_needed(self, address):
        # type: (str) -> None
        self._addresses_to_keep.add(address)

    def _is_needed(self, address):
        # type: (str) -> bool
        return address in self._addresses_to_keep

    def on_span_finish(self, span):
        # type: (Span) -> None
        try:
            if span.span_type != SpanTypes.WEB:
                return
            # Force to set respond headers at the end
            headers_req = _context.get_item(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, span=span)
            if headers_req:
                _set_headers(span, headers_req, kind="response")

            # this call is only necessary for tests or frameworks that are not using blocking
            if span.get_tag(APPSEC.JSON) is None:
                log.debug("metrics waf call")
                _asm_request_context.call_waf_callback()

            self._ddwaf._at_request_end()
        finally:
            # release asm context if it was created by the span
            _asm_request_context.unregister(span)
