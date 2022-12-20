import json
import os
from typing import TYPE_CHECKING

from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.remoteconfig.constants import ASM_DATA_PRODUCT
from ddtrace.internal.remoteconfig.constants import ASM_DD_PRODUCT
from ddtrace.internal.remoteconfig.constants import ASM_FEATURES_PRODUCT
from ddtrace.internal.remoteconfig.constants import ASM_PRODUCT
from ddtrace.internal.utils.formats import asbool


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable

    try:
        from typing import Literal
    except ImportError:
        # Python < 3.8
        from typing_extensions import Literal  # type: ignore
    from typing import Mapping
    from typing import Optional
    from typing import Union

    from ddtrace import Tracer
    from ddtrace.internal.remoteconfig.client import ConfigMetadata

log = get_logger(__name__)


def enable_appsec_rc(tracer):
    # type: (Tracer) -> None
    if _appsec_rc_features_is_enabled():
        RemoteConfig.register(ASM_FEATURES_PRODUCT, appsec_rc_reload_features(tracer))

    if tracer._appsec_enabled:
        RemoteConfig.register(ASM_DATA_PRODUCT, appsec_rc_reload_features(tracer))  # IP Blocking
        RemoteConfig.register(ASM_PRODUCT, appsec_rc_reload_features(tracer))  # Exclusion Filters & Custom Rules
        RemoteConfig.register(ASM_DD_PRODUCT, appsec_rc_reload_features(tracer))  # DD Rules


def _appsec_rules_data(tracer, features):
    # type: (Tracer, Mapping[str, Any]) -> None
    if features and tracer._appsec_processor:
        rules = []  # type: list[Any]

        def loading(feature, message):
            # type: (str, str) -> None
            rules = features.get(feature, [])
            if rules:
                log_message = "Reloading Appsec " + message + ": %s"
                log.debug(log_message, rules)
                try:
                    rules += json.loads(rules)
                except json.decoder.JSONDecodeError:
                    log_message = "ERROR Appsec " + message + ": json not parsable"
                    log.error(log_message)

        loading("rules_data", "rules data")
        loading("exclusions", "exclusion filters")
        loading("custom_rules", "custom rules")
        loading("rules", "Datadog rules")
        if rules:
            tracer._appsec_processor.update_rules(json.dumps(rules))


def _appsec_1click_actication(tracer, features):
    # type: (Tracer, Union[Literal[False], Mapping[str, Any]]) -> None
    if features is False:
        rc_appsec_enabled = False
    else:
        rc_appsec_enabled = features.get("asm", {}).get("enabled")

    if rc_appsec_enabled is not None:
        log.debug("Reloading Appsec 1-click: %s", rc_appsec_enabled)
        _appsec_enabled = True

        if not (APPSEC_ENV not in os.environ and rc_appsec_enabled is True) and (
            asbool(os.environ.get(APPSEC_ENV)) is False or rc_appsec_enabled is False
        ):
            _appsec_enabled = False
            RemoteConfig.unregister(ASM_DATA_PRODUCT)
        else:
            RemoteConfig.register(ASM_DATA_PRODUCT, appsec_rc_reload_features(tracer))

        tracer.configure(appsec_enabled=_appsec_enabled)


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
            log.debug("Updating Appsec Remote Configuration: %s", features)
            _appsec_rules_data(tracer, features)
            _appsec_1click_actication(tracer, features)

    return _reload_features
