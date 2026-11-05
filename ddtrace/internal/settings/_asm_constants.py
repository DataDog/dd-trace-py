"""AppSec configuration values shared by settings and product code."""

from typing import Literal

from ddtrace.internal.constants import Constant_Class


class LOGIN_EVENTS_MODE(metaclass=Constant_Class):
    """
    string identifier for the mode of the user login events. Can be:
    DISABLED: automatic login events are disabled. Can still be enabled by Remote Config.
    ANONYMIZATION: automatic login events are enabled but will only store non-PII fields (id, pk uid...)
    EXTENDED: automatic login events are enabled and will store potentially PII fields (username,
    email, ...).
    SDK: manually issued login events using the SDK.
    """

    DISABLED: Literal["disabled"] = "disabled"
    IDENT: Literal["identification"] = "identification"
    ANON: Literal["anonymization"] = "anonymization"
    SDK: Literal["sdk"] = "sdk"
    AUTO: Literal["auto"] = "auto"


APPSEC_ENABLED_ORIGIN_DEFAULT: Literal["default"] = "default"
APPSEC_ENABLED_ORIGIN_ENV: Literal["env_var"] = "env_var"

WAF_TIMEOUT = 5.0  # float (milliseconds)
APPSEC_OBFUSCATION_PARAMETER_KEY_REGEXP = (
    r"(?i)pass|pw(?:or)?d|secret|(?:api|private|public|access)[_-]?key|token|consumer[_-]?"
    r"(?:id|key|secret)|sign(?:ed|ature)|bearer|authorization|jsessionid|phpsessid|asp\.net[_-]sessionid|sid|jwt"
)
APPSEC_OBFUSCATION_PARAMETER_VALUE_REGEXP = (
    r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:[_-]?phrase)?|secret(?:[_-]?key)?|(?:(?:api|private|public|access)[_-]?)"
    r"key(?:[_-]?id)?|(?:(?:auth|access|id|refresh)[_-]?)?token|consumer[_-]?(?:id|key|secret)|sign(?:ed|ature)?"
    r"|auth(?:entication|orization)?|jsessionid|phpsessid|asp\.net(?:[_-]|-)sessionid|sid|jwt)"
    r'(?:\s*=([^;&]+)|"\s*:\s*("[^"]+"|\d+))|bearer\s+([a-z0-9\._\-]+)|token\s*:\s*([a-z0-9]{13})'
    r"|gh[opsu]_([0-9a-zA-Z]{36})"
    r"|ey[I-L][\w=-]+\.(ey[I-L][\w=-]+(?:\.[\w.+\/=-]+)?)|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY[\-]{5}([^\-]+)[\-]"
    r"{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*([a-z0-9\/\.+]{100,})"
)
