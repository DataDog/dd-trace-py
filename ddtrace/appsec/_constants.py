import os
from typing import Any
from typing import Iterator

from ddtrace.internal.constants import HTTP_REQUEST_BLOCKED
from ddtrace.internal.constants import REQUEST_PATH_PARAMS
from ddtrace.internal.constants import RESPONSE_HEADERS
from ddtrace.internal.constants import STATUS_403_TYPE_AUTO


class Constant_Class(type):
    """
    metaclass for Constant Classes
    - You can access constants with APPSEC.ENV or APPSEC["ENV"]
    - Direct assignment will fail: APPSEC.ENV = "something" raise TypeError, like other immutable types
    - Constant Classes can be iterated:
        for constant_name, constant_value in APPSEC: ...
    """

    def __setattr__(self, __name: str, __value: Any) -> None:
        raise TypeError("Constant class does not support item assignment: %s.%s" % (self.__name__, __name))

    def __iter__(self) -> Iterator[str]:
        def aux():
            for t in self.__dict__.items():
                if not t[0].startswith("_"):
                    yield t

        return aux()

    def get(self, k: str, default: Any = None) -> Any:
        return self.__dict__.get(k, default)

    def __contains__(self, k: str) -> bool:
        return k in self.__dict__

    def __getitem__(self, k: str) -> Any:
        return self.__dict__[k]


class APPSEC(metaclass=Constant_Class):
    """Specific constants for AppSec"""

    ENV = "DD_APPSEC_ENABLED"
    ENABLED = "_dd.appsec.enabled"
    JSON = "_dd.appsec.json"
    STRUCT = "appsec"
    EVENT_RULE_VERSION = "_dd.appsec.event_rules.version"
    EVENT_RULE_ERRORS = "_dd.appsec.event_rules.errors"
    EVENT_RULE_LOADED = "_dd.appsec.event_rules.loaded"
    EVENT_RULE_ERROR_COUNT = "_dd.appsec.event_rules.error_count"
    WAF_DURATION = "_dd.appsec.waf.duration"
    WAF_DURATION_EXT = "_dd.appsec.waf.duration_ext"
    WAF_TIMEOUTS = "_dd.appsec.waf.timeouts"
    WAF_VERSION = "_dd.appsec.waf.version"
    RASP_DURATION = "_dd.appsec.rasp.duration"
    RASP_DURATION_EXT = "_dd.appsec.rasp.duration_ext"
    RASP_RULE_EVAL = "_dd.appsec.rasp.rule.eval"
    ORIGIN_VALUE = "appsec"
    CUSTOM_EVENT_PREFIX = "appsec.events"
    USER_LOGIN_EVENT_PREFIX = "_dd.appsec.events.users.login"
    USER_LOGIN_EVENT_PREFIX_PUBLIC = "appsec.events.users.login"
    USER_LOGIN_EVENT_SUCCESS_TRACK = "appsec.events.users.login.success.track"
    USER_LOGIN_EVENT_FAILURE_TRACK = "appsec.events.users.login.failure.track"
    USER_SIGNUP_EVENT = "appsec.events.users.signup.track"
    AUTO_LOGIN_EVENTS_SUCCESS_MODE = "_dd.appsec.events.users.login.success.auto.mode"
    AUTO_LOGIN_EVENTS_FAILURE_MODE = "_dd.appsec.events.users.login.failure.auto.mode"
    BLOCKED = "appsec.blocked"
    EVENT = "appsec.event"
    AUTOMATIC_USER_EVENTS_TRACKING = "DD_APPSEC_AUTOMATED_USER_EVENTS_TRACKING"
    USER_MODEL_LOGIN_FIELD = "DD_USER_MODEL_LOGIN_FIELD"
    USER_MODEL_EMAIL_FIELD = "DD_USER_MODEL_EMAIL_FIELD"
    USER_MODEL_NAME_FIELD = "DD_USER_MODEL_NAME_FIELD"


class IAST(metaclass=Constant_Class):
    """Specific constants for IAST"""

    ENV = "DD_IAST_ENABLED"
    ENV_DEBUG = "_DD_IAST_DEBUG"
    TELEMETRY_REPORT_LVL = "DD_IAST_TELEMETRY_VERBOSITY"
    LAZY_TAINT = "_DD_IAST_LAZY_TAINT"
    JSON = "_dd.iast.json"
    ENABLED = "_dd.iast.enabled"
    CONTEXT_KEY = "_iast_data"
    PATCH_MODULES = "_DD_IAST_PATCH_MODULES"
    DENY_MODULES = "_DD_IAST_DENY_MODULES"
    SEP_MODULES = ","
    REQUEST_IAST_ENABLED = "_dd.iast.request_enabled"
    TEXT_TYPES = (str, bytes, bytearray)


class IAST_SPAN_TAGS(metaclass=Constant_Class):
    """Specific constants for IAST span tags"""

    TELEMETRY_REQUEST_TAINTED = "_dd.iast.telemetry.request.tainted"
    TELEMETRY_EXECUTED_SINK = "_dd.iast.telemetry.executed.sink"


class WAF_DATA_NAMES(metaclass=Constant_Class):
    """string names used by the waf library for requesting data from requests"""

    # PERSISTENT ADDRESSES
    REQUEST_BODY = "server.request.body"
    REQUEST_QUERY = "server.request.query"
    REQUEST_HEADERS_NO_COOKIES = "server.request.headers.no_cookies"
    REQUEST_URI_RAW = "server.request.uri.raw"
    REQUEST_METHOD = "server.request.method"
    REQUEST_PATH_PARAMS = "server.request.path_params"
    REQUEST_COOKIES = "server.request.cookies"
    REQUEST_HTTP_IP = "http.client_ip"
    REQUEST_USER_ID = "usr.id"
    RESPONSE_STATUS = "server.response.status"
    RESPONSE_HEADERS_NO_COOKIES = "server.response.headers.no_cookies"
    RESPONSE_BODY = "server.response.body"
    PERSISTENT_ADDRESSES = frozenset(
        (
            REQUEST_BODY,
            REQUEST_QUERY,
            REQUEST_HEADERS_NO_COOKIES,
            REQUEST_URI_RAW,
            REQUEST_METHOD,
            REQUEST_PATH_PARAMS,
            REQUEST_COOKIES,
            REQUEST_HTTP_IP,
            REQUEST_USER_ID,
            RESPONSE_STATUS,
            RESPONSE_HEADERS_NO_COOKIES,
            RESPONSE_BODY,
        )
    )

    # EPHEMERAL ADDRESSES
    PROCESSOR_SETTINGS = "waf.context.processor"
    LFI_ADDRESS = "server.io.fs.file"
    SSRF_ADDRESS = "server.io.net.url"


class SPAN_DATA_NAMES(metaclass=Constant_Class):
    """string names used by the library for tagging data from requests in context or span"""

    REQUEST_BODY = "http.request.body"
    REQUEST_QUERY = "http.request.query"
    REQUEST_HEADERS_NO_COOKIES = "http.request.headers"
    REQUEST_HEADERS_NO_COOKIES_CASE = "http.request.headers_case_sensitive"
    REQUEST_URI_RAW = "http.request.uri"
    REQUEST_ROUTE = "http.request.route"
    REQUEST_METHOD = "http.request.method"
    REQUEST_PATH_PARAMS = REQUEST_PATH_PARAMS
    REQUEST_COOKIES = "http.request.cookies"
    REQUEST_HTTP_IP = "http.request.remote_ip"
    REQUEST_USER_ID = "usr.id"
    RESPONSE_STATUS = "http.response.status"
    RESPONSE_HEADERS_NO_COOKIES = RESPONSE_HEADERS
    RESPONSE_BODY = "http.response.body"


class API_SECURITY(metaclass=Constant_Class):
    """constants related to API Security"""

    ENABLED = "_dd.appsec.api_security.enabled"
    ENV_VAR_ENABLED = "DD_API_SECURITY_ENABLED"
    PARSE_RESPONSE_BODY = "DD_API_SECURITY_PARSE_RESPONSE_BODY"
    REQUEST_HEADERS_NO_COOKIES = "_dd.appsec.s.req.headers"
    REQUEST_COOKIES = "_dd.appsec.s.req.cookies"
    REQUEST_QUERY = "_dd.appsec.s.req.query"
    REQUEST_PATH_PARAMS = "_dd.appsec.s.req.params"
    REQUEST_BODY = "_dd.appsec.s.req.body"
    RESPONSE_HEADERS_NO_COOKIES = "_dd.appsec.s.res.headers"
    RESPONSE_BODY = "_dd.appsec.s.res.body"
    SAMPLE_RATE = "DD_API_SECURITY_REQUEST_SAMPLE_RATE"
    SAMPLE_DELAY = "DD_API_SECURITY_SAMPLE_DELAY"
    MAX_PAYLOAD_SIZE = 0x1000000  # 16MB maximum size


class WAF_CONTEXT_NAMES(metaclass=Constant_Class):
    """string names used by the library for tagging data from requests in context"""

    RESULTS = "http.request.waf.results"
    BLOCKED = HTTP_REQUEST_BLOCKED
    CALLBACK = "http.request.waf.callback"


class WAF_ACTIONS(metaclass=Constant_Class):
    """string identifier for actions returned by the waf"""

    BLOCK = "block"
    PARAMETERS = "parameters"
    TYPE = "type"
    ID = "id"
    DEFAULT_PARAMETERS = STATUS_403_TYPE_AUTO
    BLOCK_ACTION = "block_request"
    REDIRECT_ACTION = "redirect_request"
    STACK_ACTION = "generate_stack"
    DEFAULT_ACTIONS = {
        BLOCK: {
            ID: BLOCK,
            TYPE: BLOCK_ACTION,
            PARAMETERS: DEFAULT_PARAMETERS,
        }
    }


class PRODUCTS(metaclass=Constant_Class):
    """string identifier for remote config products"""

    ASM = "ASM"
    ASM_DATA = "ASM_DATA"
    ASM_DD = "ASM_DD"
    ASM_FEATURES = "ASM_FEATURES"


class LOGIN_EVENTS_MODE(metaclass=Constant_Class):
    """
    string identifier for the mode of the user login events. Can be:
    DISABLED: automatic login events are disabled.
    SAFE: automatic login events are enabled but will only store non-PII fields (id, pk uid...)
    EXTENDED: automatic login events are enabled and will store potentially PII fields (username,
    email, ...).
    SDK: manually issued login events using the SDK.
    """

    DISABLED = "disabled"
    SAFE = "safe"
    EXTENDED = "extended"
    SDK = "sdk"


class DEFAULT(metaclass=Constant_Class):
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    RULES = os.path.join(ROOT_DIR, "rules.json")
    TRACE_RATE_LIMIT = 100
    WAF_TIMEOUT = 5.0  # float (milliseconds)
    APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP = (
        rb"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?"
        rb"(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization"
    )
    APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP = (
        rb"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)"
        rb"key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)"
        rb'(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}'
        rb"|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]"
        rb"{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}"
    )


class EXPLOIT_PREVENTION(metaclass=Constant_Class):
    STACK_TRACES = "_dd.stack"
    STACK_TRACE_ID = "stack_id"
    EP_ENABLED = "DD_APPSEC_RASP_ENABLED"
    STACK_TRACE_ENABLED = "DD_APPSEC_STACK_TRACE_ENABLED"
    MAX_STACK_TRACES = "DD_APPSEC_MAX_STACK_TRACES"
    MAX_STACK_TRACE_DEPTH = "DD_APPSEC_MAX_STACK_TRACE_DEPTH"

    class TYPE(metaclass=Constant_Class):
        LFI = "lfi"
        SSRF = "ssrf"
        SQLI = "sql_injection"

    class ADDRESS(metaclass=Constant_Class):
        LFI = "LFI_ADDRESS"
        SSRF = "SSRF_ADDRESS"
