from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.matching import getenv


def _appsec_rc_features_is_enabled():
    # type: () -> bool
    if asbool(getenv("DD_REMOTE_CONFIGURATION_ENABLED", "false")):
        return getenv(APPSEC_ENV) is None
    return False


def _appsec_rc_capabilities():
    # type: () -> str
    """return the bit of the composed capabilities in base64
    bit 0: Reserved
    bit 1: ASM Activation
    bit 2: ASM Ip blocking

    TODO: refactor to compose the string and encode it
    """
    if _appsec_rc_features_is_enabled():
        return "Ag=="
    return ""
