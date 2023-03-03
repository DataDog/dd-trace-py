import os
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool


try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable

    try:
        from typing import Literal
    except ImportError:
        # Python < 3.8. The "type ignore" is to avoid a runtime check just to silence mypy.
        from typing_extensions import Literal  # type: ignore
    from typing import Mapping
    from typing import Optional
    from typing import Union

    from ddtrace import Tracer
    from ddtrace.internal.remoteconfig.client import ConfigMetadata

log = get_logger(__name__)


def enable_appsec_rc(test_tracer=None):
    # type: (Optional[Tracer]) -> None
    # Tracer is a parameter for testing propose
    # Import tracer here to avoid a circular import
    if test_tracer is None:
        from ddtrace import tracer
    else:
        tracer = test_tracer

    if _appsec_rc_features_is_enabled():
        from ddtrace.internal.remoteconfig import RemoteConfig

        RemoteConfig.register(PRODUCTS.ASM_FEATURES, appsec_rc_reload_features(tracer))

    if tracer._appsec_enabled:
        from ddtrace.internal.remoteconfig import RemoteConfig

        RemoteConfig.register(PRODUCTS.ASM_DATA, appsec_rc_reload_features(tracer))  # IP Blocking
        RemoteConfig.register(PRODUCTS.ASM, appsec_rc_reload_features(tracer))  # Exclusion Filters & Custom Rules
        RemoteConfig.register(PRODUCTS.ASM_DD, appsec_rc_reload_features(tracer))  # DD Rules


def _add_rules_to_list(features, feature, message, rule_list):
    # type: (Mapping[str, Any], str, str, list[Any]) -> None
    rules = features.get(feature, [])
    if rules:
        try:
            rule_list += rules
            log.debug("Reloading Appsec %s: %s", message, rules)
        except JSONDecodeError:
            log.error("ERROR Appsec %s: invalid JSON content from remote configuration", message)


def _appsec_rules_data(tracer, features):
    # type: (Tracer, Mapping[str, Any]) -> None
    if features and tracer._appsec_processor:
        ruleset = {"rules": [], "rules_data": []}  # type: dict[str, list[Any]]
        _add_rules_to_list(features, "rules_data", "rules data", ruleset["rules_data"])
        _add_rules_to_list(features, "custom_rules", "custom rules", ruleset["rules"])
        _add_rules_to_list(features, "rules", "Datadog rules", ruleset["rules"])
        if any(ruleset.values()):
            tracer._appsec_processor._update_rules({k: v for k, v in ruleset.items() if v})


def _appsec_1click_activation(tracer, features):
    # type: (Tracer, Union[Literal[False], Mapping[str, Any]]) -> None
    if APPSEC_ENV in os.environ:
        # no one click activation if var env is set
        rc_appsec_enabled = asbool(os.environ.get(APPSEC_ENV))
    elif features is False:
        rc_appsec_enabled = False
    else:
        rc_appsec_enabled = features.get("asm", {}).get("enabled")

    if rc_appsec_enabled is not None:
        from ddtrace.appsec._constants import PRODUCTS
        from ddtrace.internal.remoteconfig import RemoteConfig

        log.debug("Reloading Appsec 1-click: %s", rc_appsec_enabled)

        if rc_appsec_enabled:
            RemoteConfig.register(PRODUCTS.ASM_DATA, appsec_rc_reload_features(tracer))  # IP Blocking
            RemoteConfig.register(PRODUCTS.ASM, appsec_rc_reload_features(tracer))  # Exclusion Filters & Custom Rules
            RemoteConfig.register(PRODUCTS.ASM_DD, appsec_rc_reload_features(tracer))  # DD Rules
            if not tracer._appsec_enabled:
                tracer.configure(appsec_enabled=True)
            else:
                config._appsec_enabled = True

        else:
            RemoteConfig.unregister(PRODUCTS.ASM_DATA)
            RemoteConfig.unregister(PRODUCTS.ASM)
            RemoteConfig.unregister(PRODUCTS.ASM_DD)
            if tracer._appsec_enabled:
                tracer.configure(appsec_enabled=False)
            else:
                config._appsec_enabled = False


def appsec_rc_reload_features(tracer):
    # type: (Tracer) -> Callable
    def _reload_features(metadata, features):
        # type: (Optional[ConfigMetadata], Optional[Mapping[str, Any]]) -> None
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

        if features is not None:
            log.debug("Updating ASM Remote Configuration: %s", features)
            # The order of this matters since 1click could reconfigure the AppSecProcessor
            # which the second checks
            _appsec_1click_activation(tracer, features)
            _appsec_rules_data(tracer, features)

    return _reload_features
