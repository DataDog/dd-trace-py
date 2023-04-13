import os
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.v2._connectors import ConnectorSharedMemoryJson
from ddtrace.internal.remoteconfig.v2._pubsub import PubSubBase
from ddtrace.internal.remoteconfig.v2._pubsub import PubSubMergeFirst
from ddtrace.internal.remoteconfig.v2._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.v2.worker import remoteconfig_poller
from ddtrace.internal.utils.formats import asbool


try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Dict
    from typing import Mapping
    from typing import Optional

    from ddtrace import Tracer

log = get_logger(__name__)


class AppSecRC(PubSubMergeFirst):
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data = ConnectorSharedMemoryJson()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data, callback, "ASM")


def enable_appsec_rc(test_tracer=None, start_subscribers=True):
    # type: (Optional[Tracer], Any) -> None
    # Tracer is a parameter for testing propose
    # Import tracer here to avoid a circular import
    if test_tracer is None:
        from ddtrace import tracer
    else:
        tracer = test_tracer

    log.debug("[%s][P: %s] Register ASM Remote Config Callback", os.getpid(), os.getppid())
    asm_callback = AppSecRC(_preprocess_results_appsec_1click_activation, _appsec_callback)
    if _appsec_rc_features_is_enabled():
        remoteconfig_poller.register(PRODUCTS.ASM_FEATURES, asm_callback)

    if tracer._appsec_enabled:
        remoteconfig_poller.register(PRODUCTS.ASM_DATA, asm_callback)  # IP Blocking
        remoteconfig_poller.register(PRODUCTS.ASM, asm_callback)  # Exclusion Filters & Custom Rules
        remoteconfig_poller.register(PRODUCTS.ASM_DD, asm_callback)  # DD Rules
    if start_subscribers:
        asm_callback.start_subscriber()


def _add_rules_to_list(features, feature, message, ruleset):
    # type: (Mapping[str, Any], str, str, Dict[str, Any]) -> None
    rules = features.get(feature, None)
    if rules is not None:
        try:
            if ruleset.get(feature) is None:
                ruleset[feature] = []
            ruleset[feature] += rules
            log.debug("Reloading Appsec %s: %s", message, str(rules)[:20])
        except JSONDecodeError:
            log.error("ERROR Appsec %s: invalid JSON content from remote configuration", message)


def _appsec_callback(features, test_tracer=None):
    # type: (Mapping[str, Any], Optional[Tracer]) -> None
    _appsec_1click_activation(features, test_tracer)
    _appsec_rules_data(features, test_tracer)


def _appsec_rules_data(features, test_tracer):
    # type: (Mapping[str, Any], Optional[Tracer]) -> bool
    # Tracer is a parameter for testing propose
    # Import tracer here to avoid a circular import
    if test_tracer is None:
        from ddtrace import tracer
    else:
        tracer = test_tracer

    if features and tracer._appsec_processor:
        ruleset = {}  # type: dict[str, Optional[list[Any]]]
        _add_rules_to_list(features, "rules_data", "rules data", ruleset)
        _add_rules_to_list(features, "custom_rules", "custom rules", ruleset)
        _add_rules_to_list(features, "rules", "Datadog rules", ruleset)
        _add_rules_to_list(features, "exclusions", "exclusion filters", ruleset)
        _add_rules_to_list(features, "rules_override", "rules override", ruleset)
        return tracer._appsec_processor._update_rules({k: v for k, v in ruleset.items() if v is not None})

    return False


def _preprocess_results_appsec_1click_activation(features, pubsub_instance=None):
    # type: (Any, Optional[PubSubBase]) -> Mapping[str, Any]
    if not pubsub_instance:
        pubsub_instance = AppSecRC(_preprocess_results_appsec_1click_activation, _appsec_callback)

    rc_appsec_enabled = None
    if features is not None:
        if APPSEC_ENV in os.environ:
            rc_appsec_enabled = asbool(os.environ.get(APPSEC_ENV))
        elif features is False or features == {}:
            rc_appsec_enabled = False
        else:
            asm_features = features.get("asm", {})
            if asm_features is not None:
                rc_appsec_enabled = asm_features.get("enabled")

        if rc_appsec_enabled is not None:
            from ddtrace.appsec._constants import PRODUCTS

            log.debug(
                "[%s][P: %s] Updating ASM Remote Configuration ASM_FEATURES: %s",
                os.getpid(),
                os.getppid(),
                rc_appsec_enabled,
            )

            if rc_appsec_enabled:
                remoteconfig_poller.register(PRODUCTS.ASM_DATA, pubsub_instance)  # IP Blocking
                remoteconfig_poller.register(PRODUCTS.ASM, pubsub_instance)  # Exclusion Filters & Custom Rules
                remoteconfig_poller.register(PRODUCTS.ASM_DD, pubsub_instance)  # DD Rules
            else:
                remoteconfig_poller.unregister(PRODUCTS.ASM_DATA)
                remoteconfig_poller.unregister(PRODUCTS.ASM)
                remoteconfig_poller.unregister(PRODUCTS.ASM_DD)
    if features is False:
        features = {"asm": {"enabled": rc_appsec_enabled}}
    else:
        features["asm"] = {"enabled": rc_appsec_enabled}
    return features


def _appsec_1click_activation(features, test_tracer=None):
    # type: (Mapping[str, Any], Optional[Tracer]) -> None
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
    # Tracer is a parameter for testing propose
    # Import tracer here to avoid a circular import
    if test_tracer is None:
        from ddtrace import tracer
    else:
        tracer = test_tracer

    log.debug("[%s][P: %s] ASM_FEATURES: %s", os.getpid(), os.getppid(), str(features)[:100])
    if APPSEC_ENV in os.environ:
        # no one click activation if var env is set
        rc_appsec_enabled = asbool(os.environ.get(APPSEC_ENV))
    elif features is False:
        rc_appsec_enabled = False
    else:
        rc_appsec_enabled = features.get("asm", {}).get("enabled")

    log.debug("APPSEC_ENABLED: %s", rc_appsec_enabled)
    if rc_appsec_enabled is not None:

        log.debug(
            "[%s][P: %s] Updating ASM Remote Configuration ASM_FEATURES: %s",
            os.getpid(),
            os.getppid(),
            rc_appsec_enabled,
        )

        if rc_appsec_enabled:
            if not tracer._appsec_enabled:
                tracer.configure(appsec_enabled=True)
            else:
                config._appsec_enabled = True
        else:
            if tracer._appsec_enabled:
                tracer.configure(appsec_enabled=False)
            else:
                config._appsec_enabled = False
