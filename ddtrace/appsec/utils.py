import os

from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.utils.formats import asbool


def _appsec_rc_features_is_enabled():
    if asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "true")):
        return APPSEC_ENV not in os.environ
    return False


def _appsec_rc_capabilities():
    if _appsec_rc_features_is_enabled():
        return "Ag=="
    return ""
