import os

from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.utils.formats import asbool


def _appsec_rc_features_is_enabled():
    # type: () -> bool
    if asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "false")):
        return APPSEC_ENV not in os.environ
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
