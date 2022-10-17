import errno
import json
import os
import os.path
from typing import List
from typing import Set
from typing import TYPE_CHECKING
from typing import Tuple
from typing import Union

import attr
from six import ensure_binary

from ddtrace.appsec._ddwaf import DDWaf
from ddtrace.appsec._ddwaf import version
from ddtrace.constants import APPSEC_ENABLED
from ddtrace.constants import APPSEC_EVENT_RULE_ERRORS
from ddtrace.constants import APPSEC_EVENT_RULE_ERROR_COUNT
from ddtrace.constants import APPSEC_EVENT_RULE_LOADED
from ddtrace.constants import APPSEC_EVENT_RULE_VERSION
from ddtrace.constants import APPSEC_JSON
from ddtrace.constants import APPSEC_WAF_DURATION
from ddtrace.constants import APPSEC_WAF_DURATION_EXT
from ddtrace.constants import APPSEC_WAF_VERSION
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import ORIGIN_KEY
from ddtrace.constants import RUNTIME_FAMILY
from ddtrace.contrib.trace_utils import _normalize_tag_name
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from ddtrace.internal.logger import get_logger
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.rate_limiter import RateLimiter


if TYPE_CHECKING:
    from typing import Dict

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


class _Addresses(object):
    SERVER_REQUEST_BODY = "server.request.body"
    SERVER_REQUEST_QUERY = "server.request.query"
    SERVER_REQUEST_HEADERS_NO_COOKIES = "server.request.headers.no_cookies"
    SERVER_REQUEST_URI_RAW = "server.request.uri.raw"
    SERVER_REQUEST_METHOD = "server.request.method"
    SERVER_REQUEST_PATH_PARAMS = "server.request.path_params"
    SERVER_REQUEST_COOKIES = "server.request.cookies"
    SERVER_RESPONSE_STATUS = "server.response.status"
    SERVER_RESPONSE_HEADERS_NO_COOKIES = "server.response.headers.no_cookies"


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
    for k in headers:
        if k.lower() in _COLLECTED_REQUEST_HEADERS:
            # since the header value can be a list, use `set_tag()` to ensure it is converted to a string
            span.set_tag(_normalize_tag_name(kind, k), headers[k])


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
        self._mark_needed(_Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES)
        # we always need the response headers
        self._mark_needed(_Addresses.SERVER_RESPONSE_HEADERS_NO_COOKIES)

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def _mark_needed(self, address):
        # type: (str) -> None
        self._addresses_to_keep.add(address)

    def _is_needed(self, address):
        # type: (str) -> bool
        return address in self._addresses_to_keep

    def on_span_finish(self, span):
        # type: (Span) -> None
        if span.span_type != SpanTypes.WEB:
            return
        span.set_metric(APPSEC_ENABLED, 1.0)
        span._set_str_tag(RUNTIME_FAMILY, "python")

        data = {}
        if self._is_needed(_Addresses.SERVER_REQUEST_QUERY):
            request_query = _context.get_item("http.request.query", span=span)
            if request_query is not None:
                data[_Addresses.SERVER_REQUEST_QUERY] = request_query

        if self._is_needed(_Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES):
            request_headers = _context.get_item("http.request.headers", span=span)
            if request_headers is not None:
                data[_Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES] = _transform_headers(request_headers)

        if self._is_needed(_Addresses.SERVER_REQUEST_URI_RAW):
            uri = _context.get_item("http.request.uri", span=span)
            if uri is not None:
                data[_Addresses.SERVER_REQUEST_URI_RAW] = uri

        if self._is_needed(_Addresses.SERVER_REQUEST_METHOD):
            request_method = _context.get_item("http.request.method", span=span)
            if request_method is not None:
                data[_Addresses.SERVER_REQUEST_METHOD] = request_method

        if self._is_needed(_Addresses.SERVER_REQUEST_PATH_PARAMS):
            path_params = _context.get_item("http.request.path_params", span=span)
            if path_params is not None:
                data[_Addresses.SERVER_REQUEST_PATH_PARAMS] = path_params

        if self._is_needed(_Addresses.SERVER_REQUEST_COOKIES):
            cookies = _context.get_item("http.request.cookies", span=span)
            if cookies is not None:
                data[_Addresses.SERVER_REQUEST_COOKIES] = cookies

        if self._is_needed(_Addresses.SERVER_RESPONSE_STATUS):
            status = _context.get_item("http.response.status", span=span)
            if status is not None:
                data[_Addresses.SERVER_RESPONSE_STATUS] = status

        if self._is_needed(_Addresses.SERVER_RESPONSE_HEADERS_NO_COOKIES):
            response_headers = _context.get_item("http.response.headers", span=span)
            if response_headers is not None:
                data[_Addresses.SERVER_RESPONSE_HEADERS_NO_COOKIES] = _transform_headers(response_headers)

        if self._is_needed(_Addresses.SERVER_REQUEST_BODY):
            body = _context.get_item("http.request.body", span=span)
            if body is not None:
                data[_Addresses.SERVER_REQUEST_BODY] = body

        log.debug("[DDAS-001-00] Executing AppSec In-App WAF with parameters: %s", data)
        res, total_runtime, total_overall_runtime = self._ddwaf.run(data, self._waf_timeout)  # res is a serialized json

        try:
            info = self._ddwaf.info
            if info["errors"]:
                span._set_str_tag(APPSEC_EVENT_RULE_ERRORS, json.dumps(info["errors"]))
            span._set_str_tag(APPSEC_EVENT_RULE_VERSION, info["version"])
            span._set_str_tag(APPSEC_WAF_VERSION, "%s.%s.%s" % version())

            span.set_metric(APPSEC_EVENT_RULE_LOADED, info["loaded"])
            span.set_metric(APPSEC_EVENT_RULE_ERROR_COUNT, info["failed"])
            span.set_metric(APPSEC_WAF_DURATION, total_runtime)
            span.set_metric(APPSEC_WAF_DURATION_EXT, total_overall_runtime)
        except (json.decoder.JSONDecodeError, ValueError):
            log.warning("Error parsing data AppSec In-App WAF metrics report")
        except Exception:
            log.warning("Error executing AppSec In-App WAF metrics report: %s", exc_info=True)
        if res is not None:
            # We run the rate limiter only if there is an attack, its goal is to limit the number of collected asm
            # events
            allowed = self._rate_limiter.is_allowed(span.start_ns)
            if not allowed:
                # TODO: add metric collection to keep an eye (when it's name is clarified)
                return
            if _Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES in data:
                _set_headers(span, data[_Addresses.SERVER_REQUEST_HEADERS_NO_COOKIES], kind="request")

            if _Addresses.SERVER_RESPONSE_HEADERS_NO_COOKIES in data:
                _set_headers(span, data[_Addresses.SERVER_RESPONSE_HEADERS_NO_COOKIES], kind="response")
            # Partial DDAS-011-00
            log.debug("[DDAS-011-00] AppSec In-App WAF returned: %s", res)
            span._set_str_tag("appsec.event", "true")
            span._set_str_tag(APPSEC_JSON, '{"triggers":%s}' % (res,))

            remote_ip = _context.get_item("http.request.remote_ip", span=span)
            if remote_ip:
                # Note that if the ip collection is disabled by the env var
                # DD_TRACE_CLIENT_IP_HEADER_DISABLED actor.ip won't be sent
                span._set_str_tag("actor.ip", remote_ip)
            # Right now, we overwrite any value that could be already there. We need to reconsider when ASM/AppSec's
            # specs are updated.
            span.set_tag(MANUAL_KEEP_KEY)
            if span.get_tag(ORIGIN_KEY) is None:
                span._set_str_tag(ORIGIN_KEY, "appsec")
