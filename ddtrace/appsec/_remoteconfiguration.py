import os

from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)


def _appsec_rc_features_is_enabled():
    if asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "true")):
        return APPSEC_ENV not in os.environ
    return False


def enable_appsec_rc(tracer):
    if _appsec_rc_features_is_enabled():
        RemoteConfig.register(ASM_FEATURES_PRODUCT, appsec_rc_reload_features(tracer))


def appsec_rc_reload_features(tracer):
    def _reload_features(metadata, features):
        """This callback updates appsec enabled in tracer and config instances following this logic:
        ```
        | DD_APPSEC_ENABLED | RC Enabled | Result   |
        |-------------------|------------|----------|
        | <not set>         | <not set>  | Disabled |
        | <not set>         | false      | Disabled |
        | <not set>         | true       | Enabled  |
        | false             | <not set>  | Disabled |
        | true              | <not set>  | Enabled  |
        | false             | true       | Disabled |
        | true              | true       | Enabled  |
        ```
        """

        if features:
            log.debug("Reloading tracer features. %r", features)
            rc_appsec_enabled = features.get("asm", {}).get("enabled")

            _appsec_enabled = True

            if not (APPSEC_ENV not in os.environ and rc_appsec_enabled is True) and (
                asbool(os.environ.get(APPSEC_ENV)) is False or rc_appsec_enabled is False
            ):
                _appsec_enabled = False

            tracer.configure(appsec_enabled=_appsec_enabled)

    return _reload_features
