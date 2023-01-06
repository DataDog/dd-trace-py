import errno
import json
import os
import os.path
from typing import Set
from typing import TYPE_CHECKING

import attr
from six import ensure_binary

from ddtrace.appsec.constants import SPAN_DATA_NAMES
from ddtrace.appsec.constants import WAF_ACTIONS
from ddtrace.appsec.constants import WAF_CONTEXT_NAMES
from ddtrace.appsec.constants import WAF_DATA_NAMES
from ddtrace.appsec.ddwaf import DDWaf
from ddtrace.appsec.ddwaf import version
from ddtrace.constants import APPSEC_ENABLED
from ddtrace.constants import APPSEC_EVENT_RULE_ERRORS
from ddtrace.constants import APPSEC_EVENT_RULE_ERROR_COUNT
from ddtrace.constants import APPSEC_EVENT_RULE_LOADED
from ddtrace.constants import APPSEC_EVENT_RULE_VERSION
from ddtrace.constants import APPSEC_JSON
from ddtrace.constants import APPSEC_ORIGIN_VALUE
from ddtrace.constants import APPSEC_WAF_DURATION
from ddtrace.constants import APPSEC_WAF_DURATION_EXT
from ddtrace.constants import APPSEC_WAF_VERSION
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


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Tuple
    from typing import Union

    from ddtrace.appsec.ddwaf import DDWaf_result
    from ddtrace.span import Span

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(ROOT_DIR, "rules.json")
DEFAULT_TRACE_RATE_LIMIT = 100
DEFAULT_WAF_TIMEOUT = 20  # ms
DEFAULT_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP = (
    r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?"
    r"(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization"
)
DEFAULT_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP = (
    r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)"
    r"key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)"
    r'(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}'
    r"|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]"
    r"{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}"
)


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
    return os.getenv("DD_APPSEC_RULES", default=DEFAULT_RULES)


def get_appsec_obfuscation_parameter_key_regexp():
    # type: () -> bytes
    return ensure_binary(
        os.getenv("DD_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP", DEFAULT_APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP)
    )


def get_appsec_obfuscation_parameter_value_regexp():
    # type: () -> bytes
    return ensure_binary(
        os.getenv("DD_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP", DEFAULT_APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP)
    )


_COLLECTED_REQUEST_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "content-encoding",
    "content-language",
    "content-length",
    "content-type",
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

_COLLECTED_HEADER_PREFIX = "http.request.headers."


def _set_headers(span, headers, kind):
    # type: (Span, Dict[str, Union[str, List[str]]], str) -> None
    for id_name, value in headers if isinstance(headers, list) else headers.items():
        if id_name.lower() in _COLLECTED_REQUEST_HEADERS:
            # since the header value can be a list, use `set_tag()` to ensure it is converted to a string
            span.set_tag(_normalize_tag_name(kind, id_name), value)


def _get_rate_limiter():
    # type: () -> RateLimiter
    return RateLimiter(int(os.getenv("DD_APPSEC_TRACE_RATE_LIMIT", DEFAULT_TRACE_RATE_LIMIT)))


def _get_waf_timeout():
    # type: () -> int
    return int(os.getenv("DD_APPSEC_WAF_TIMEOUT", DEFAULT_WAF_TIMEOUT))


@attr.s(eq=False)
class AppSecSpanProcessor(SpanProcessor):
    rules = attr.ib(type=str, factory=get_rules)
    obfuscation_parameter_key_regexp = attr.ib(type=bytes, factory=get_appsec_obfuscation_parameter_key_regexp)
    obfuscation_parameter_value_regexp = attr.ib(type=bytes, factory=get_appsec_obfuscation_parameter_value_regexp)
    _ddwaf = attr.ib(type=DDWaf, default=None)
    _addresses_to_keep = attr.ib(type=Set[str], factory=set)
    _rate_limiter = attr.ib(type=RateLimiter, factory=_get_rate_limiter)
    _waf_timeout = attr.ib(type=int, factory=_get_waf_timeout)

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
                        "[DDAS-0001-03] AppSec could not read the rule file %s. Reason: file does not exist", self.rules
                    )
                else:
                    # TODO: try to log reasons
                    log.error("[DDAS-0001-03] AppSec could not read the rule file %s.", self.rules)
                raise
            except json.decoder.JSONDecodeError:
                log.error(
                    "[DDAS-0001-03] AppSec could not read the rule file %s. Reason: invalid JSON file", self.rules
                )
                raise
            except Exception:
                # TODO: try to log reasons
                log.error("[DDAS-0001-03] AppSec could not read the rule file %s.", self.rules)
                raise
            try:
                self._ddwaf = DDWaf(
                    rules, self.obfuscation_parameter_key_regexp, self.obfuscation_parameter_value_regexp
                )
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

    def update_rules(self, new_rules):
        # type: (List[Dict[str, Any]]) -> None
        self._ddwaf.update_rules(new_rules)

    def on_span_start(self, span, *args, **kwargs):
        # type: (Span, Any, Any) -> None
        peer_ip = kwargs.get("peer_ip")
        headers = kwargs.get("headers", {})
        headers_case_sensitive = bool(kwargs.get("headers_case_sensitive"))

        _context.set_items(
            {
                SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES: headers,
                "http.request.headers_case_sensitive": headers_case_sensitive,
                WAF_CONTEXT_NAMES.CALLBACK: lambda: self._waf_action(span._local_root or span),
            },
            span=span,
        )

        if peer_ip or headers:
            ip = trace_utils._get_request_header_client_ip(span, headers, peer_ip, headers_case_sensitive)
            # Save the IP and headers in the context so the retrieval can be skipped later
            _context.set_item(SPAN_DATA_NAMES.REQUEST_HTTP_IP, ip, span=span)
            self._mark_needed(WAF_DATA_NAMES.REQUEST_HTTP_IP)
            # self._waf_action(span)

    def _waf_action(self, span):
        # type: (Span) -> None
        data = {}
        for key, waf_name in WAF_DATA_NAMES:
            if self._is_needed(waf_name):
                value = _context.get_item(SPAN_DATA_NAMES[key], span=span)
                if value is not None:
                    data[waf_name] = _transform_headers(value) if key.endswith("HEADERS_NO_COOKIES") else value
        log.debug("[DDAS-001-00] Executing AppSec In-App WAF with parameters: %s", data)
        waf_results = self._run_ddwaf(data)
        log.debug("[DDAS-011-00] AppSec In-App WAF returned: %s", waf_results.data)
        blocked = WAF_ACTIONS.BLOCK in waf_results.actions
        if blocked:
            _context.set_item(WAF_CONTEXT_NAMES.BLOCKED, True, span=span)
        if span.span_type != SpanTypes.WEB:
            return
        span.set_metric(APPSEC_ENABLED, 1.0)
        span.set_tag_str(RUNTIME_FAMILY, "python")
        try:
            info = self._ddwaf.info
            if info.errors:
                span.set_tag_str(APPSEC_EVENT_RULE_ERRORS, json.dumps(info.errors))
            span.set_tag_str(APPSEC_EVENT_RULE_VERSION, info.version)
            span.set_tag_str(APPSEC_WAF_VERSION, version())

            span.set_metric(APPSEC_EVENT_RULE_LOADED, info.loaded)
            span.set_metric(APPSEC_EVENT_RULE_ERROR_COUNT, info.failed)
            if not blocked and waf_results:
                span.set_metric(APPSEC_WAF_DURATION, waf_results.runtime)
                span.set_metric(APPSEC_WAF_DURATION_EXT, waf_results.total_runtime)
        except (json.decoder.JSONDecodeError, ValueError):
            log.warning("Error parsing data AppSec In-App WAF metrics report")
        except Exception:
            log.warning("Error executing AppSec In-App WAF metrics report: %s", exc_info=True)

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
                span.set_tag_str(APPSEC_JSON, '{"triggers":%s}' % (waf_results.data,))
            if blocked:
                span.set_tag("appsec.blocked", True)

            # Partial DDAS-011-00
            span.set_tag_str("appsec.event", "true")

            remote_ip = _context.get_item("http.request.remote_ip", span=span)
            if remote_ip:
                # Note that if the ip collection is disabled by the env var
                # DD_TRACE_CLIENT_IP_HEADER_DISABLED actor.ip won't be sent
                span.set_tag_str("actor.ip", remote_ip)

            # Right now, we overwrite any value that could be already there. We need to reconsider when ASM/AppSec's
            # specs are updated.
            span.set_tag(MANUAL_KEEP_KEY)
            if span.get_tag(ORIGIN_KEY) is None:
                span.set_tag_str(ORIGIN_KEY, APPSEC_ORIGIN_VALUE)

    def _run_ddwaf(self, data):
        # type: (dict[str, str]) -> DDWaf_result
        return self._ddwaf.run(data, self._waf_timeout)  # res is a serialized json

    def _mark_needed(self, address):
        # type: (str) -> None
        self._addresses_to_keep.add(address)

    def _is_needed(self, address):
        # type: (str) -> bool
        return address in self._addresses_to_keep

    def on_span_finish(self, span):
        # type: (Span) -> None
        pass
