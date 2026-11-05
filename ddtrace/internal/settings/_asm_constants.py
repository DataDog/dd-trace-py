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
