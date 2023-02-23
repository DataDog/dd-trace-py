import os
from typing import TYPE_CHECKING

import six


if TYPE_CHECKING:
    from typing import Any
    from typing import Iterator


class Constant_Class(type):
    """
    metaclass for Constant Classes
    - You can access constants with APPSEC.ENV or APPSEC["ENV"]
    - Direct assignment will fail: APPSEC.ENV = "something" raise TypeError, like other immutable types
    - Constant Classes can be iterated:
        for constant_name, constant_value in APPSEC: ...
    """

    def __setattr__(self, __name, __value):
        # type: ("Constant_Class", str, Any) -> None
        raise TypeError("Constant class does not support item assignment: %s.%s" % (self.__name__, __name))

    def __iter__(self):
        # type: ("Constant_Class") -> Iterator[tuple[str, Any]]
        def aux():
            for t in self.__dict__.items():
                if not t[0].startswith("_"):
                    yield t

        return aux()

    def __getitem__(self, k):
        # type: ("Constant_Class", str) -> Any
        return self.__dict__[k]


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class APPSEC(object):
    """Specific constants for AppSec"""

    ENV = "DD_APPSEC_ENABLED"
    ENABLED = "_dd.appsec.enabled"
    JSON = "_dd.appsec.json"
    EVENT_RULE_VERSION = "_dd.appsec.event_rules.version"
    EVENT_RULE_ERRORS = "_dd.appsec.event_rules.errors"
    EVENT_RULE_LOADED = "_dd.appsec.event_rules.loaded"
    EVENT_RULE_ERROR_COUNT = "_dd.appsec.event_rules.error_count"
    WAF_DURATION = "_dd.appsec.waf.duration"
    WAF_DURATION_EXT = "_dd.appsec.waf.duration_ext"
    WAF_TIMEOUTS = "_dd.appsec.waf.timeouts"
    WAF_VERSION = "_dd.appsec.waf.version"
    ORIGIN_VALUE = "appsec"
    CUSTOM_EVENT_PREFIX = "appsec.events"
    USER_LOGIN_EVENT_PREFIX = "appsec.events.users.login"
    BLOCKED = "appsec.blocked"
    EVENT = "appsec.event"


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class IAST(object):
    """Specific constants for IAST"""

    ENV = "DD_IAST_ENABLED"
    JSON = "_dd.iast.json"
    ENABLED = "_dd.iast.enabled"
    CONTEXT_KEY = "_iast_data"


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class WAF_DATA_NAMES(object):
    """string names used by the waf library for requesting data from requests"""

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


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class SPAN_DATA_NAMES(object):
    """string names used by the library for tagging data from requests in context or span"""

    REQUEST_BODY = "http.request.body"
    REQUEST_QUERY = "http.request.query"
    REQUEST_HEADERS_NO_COOKIES = "http.request.headers"
    REQUEST_HEADERS_NO_COOKIES_CASE = "http.request.headers_case_sensitive"
    REQUEST_URI_RAW = "http.request.uri"
    REQUEST_METHOD = "http.request.method"
    REQUEST_PATH_PARAMS = "http.request.path_params"
    REQUEST_COOKIES = "http.request.cookies"
    REQUEST_HTTP_IP = "http.request.remote_ip"
    REQUEST_USER_ID = "usr.id"
    RESPONSE_STATUS = "http.response.status"
    RESPONSE_HEADERS_NO_COOKIES = "http.response.headers"


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class WAF_CONTEXT_NAMES(object):
    """string names used by the library for tagging data from requests in context"""

    RESULTS = "http.request.waf.results"
    BLOCKED = "http.request.blocked"
    CALLBACK = "http.request.waf.callback"


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class WAF_ACTIONS(object):
    """string identifier for actions returned by the waf"""

    BLOCK = "block"


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class PRODUCTS(object):
    """string identifier for remote config products"""

    ASM = "ASM"
    ASM_DATA = "ASM_DATA"
    ASM_DD = "ASM_DD"
    ASM_FEATURES = "ASM_FEATURES"


@six.add_metaclass(Constant_Class)  # required for python2/3 compatibility
class DEFAULT(object):
    ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
    RULES = os.path.join(ROOT_DIR, "rules.json")
    TRACE_RATE_LIMIT = 100
    WAF_TIMEOUT = 5  # ms
    APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP = (
        r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?)key)|token|consumer_?"
        r"(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization"
    )
    APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP = (
        r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|public_?|access_?|secret_?)"
        r"key(?:_?id)?|token|consumer_?(?:id|key|secret)|sign(?:ed|ature)?|auth(?:entication|orization)?)"
        r'(?:\s*=[^;]|"\s*:\s*"[^"]+")|bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}'
        r"|ey[I-L][\w=-]+\.ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}[^\-]+[\-]"
        r"{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}"
    )
