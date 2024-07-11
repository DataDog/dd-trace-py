# -*- coding: utf-8 -*-
import json
import os
from typing import Any
from typing import Dict
from typing import Mapping
from typing import Optional

from ddtrace import Tracer
from ddtrace import config
from ddtrace.appsec._capabilities import _appsec_rc_file_is_not_static
from ddtrace.appsec._capabilities import _asm_feature_is_required
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec._utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisherMergeDicts
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.utils.formats import asbool
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

APPSEC_PRODUCTS = [PRODUCTS.ASM_FEATURES, PRODUCTS.ASM, PRODUCTS.ASM_DATA, PRODUCTS.ASM_DD]


class AppSecRC(PubSub):
    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisherMergeDicts
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, _preprocess_results, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "ASM")


def _forksafe_appsec_rc():
    remoteconfig_poller.start_subscribers_by_product(APPSEC_PRODUCTS)


def enable_appsec_rc(test_tracer: Optional[Tracer] = None) -> None:
    """Remote config will be used by ASM libraries to receive four different updates from the backend.
    Each update has it’s own product:
    - ASM_FEATURES product - To allow users enable or disable ASM remotely
    - ASM product - To allow clients to activate or deactivate rules
    - ASM_DD product - To allow the library to receive rules updates
    - ASM_DATA product - To allow the library to receive list of blocked IPs and users

    If environment variable `DD_APPSEC_ENABLED` is not set, registering ASM_FEATURE can enable ASM remotely. If
    it's set to true, we will register the rest of the products.

    Parameters `test_tracer` and `start_subscribers` are needed for testing purposes
    """
    # Import tracer here to avoid a circular import
    if test_tracer is None:
        from ddtrace import tracer
    else:
        tracer = test_tracer

    log.debug("[%s][P: %s] Register ASM Remote Config Callback", os.getpid(), os.getppid())
    asm_callback = (
        remoteconfig_poller.get_registered(PRODUCTS.ASM_FEATURES)
        or remoteconfig_poller.get_registered(PRODUCTS.ASM)
        or AppSecRC(_preprocess_results_appsec_1click_activation, _appsec_callback)
    )

    if _asm_feature_is_required():
        remoteconfig_poller.register(PRODUCTS.ASM_FEATURES, asm_callback)

    if tracer._asm_enabled and _appsec_rc_file_is_not_static():
        remoteconfig_poller.register(PRODUCTS.ASM_DATA, asm_callback)  # IP Blocking
        remoteconfig_poller.register(PRODUCTS.ASM, asm_callback)  # Exclusion Filters & Custom Rules
        remoteconfig_poller.register(PRODUCTS.ASM_DD, asm_callback)  # DD Rules

    forksafe.register(_forksafe_appsec_rc)


def disable_appsec_rc():
    # only used to avoid data leaks between tests
    for product_name in APPSEC_PRODUCTS:
        remoteconfig_poller.unregister(product_name)


def _add_rules_to_list(features: Mapping[str, Any], feature: str, message: str, ruleset: Dict[str, Any]) -> None:
    rules = features.get(feature, None)
    if rules is not None:
        try:
            if ruleset.get(feature) is None:
                ruleset[feature] = []
            ruleset[feature] += rules
            log.debug("Reloading Appsec %s: %s", message, str(rules)[:20])
        except json.JSONDecodeError:
            log.error("ERROR Appsec %s: invalid JSON content from remote configuration", message)


def _appsec_callback(features: Mapping[str, Any], test_tracer: Optional[Tracer] = None) -> None:
    config = features.get("config", {})
    _appsec_1click_activation(config, test_tracer)
    _appsec_rules_data(config, test_tracer)


def _appsec_rules_data(features: Mapping[str, Any], test_tracer: Optional[Tracer]) -> bool:
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
        _add_rules_to_list(features, "scanners", "scanners", ruleset)
        _add_rules_to_list(features, "processors", "processors", ruleset)
        _add_rules_to_list(features, "actions", "actions", ruleset)
        if ruleset:
            return tracer._appsec_processor._update_rules({k: v for k, v in ruleset.items() if v is not None})

    return False


def _preprocess_results_appsec_1click_activation(
    features: Dict[str, Any], pubsub_instance: Optional[PubSub] = None
) -> Dict[str, Any]:
    """The main process has the responsibility to enable or disable the ASM products. The child processes don't
    care about that, the children only need to know about payload content.
    """
    if _appsec_rc_features_is_enabled():
        log.debug(
            "[%s][P: %s] Receiving ASM Remote Configuration ASM_FEATURES: %s",
            os.getpid(),
            os.getppid(),
            features.get("asm", {}),
        )

        rc_asm_enabled = None
        if features is not None:
            if APPSEC_ENV in os.environ:
                rc_asm_enabled = asbool(os.environ.get(APPSEC_ENV))
            elif features == {}:
                rc_asm_enabled = False
            else:
                asm_features = features.get("asm", {})
                if asm_features is not None:
                    rc_asm_enabled = asm_features.get("enabled")
            log.debug(
                "[%s][P: %s] ASM Remote Configuration ASM_FEATURES. Appsec enabled: %s",
                os.getpid(),
                os.getppid(),
                rc_asm_enabled,
            )
            if rc_asm_enabled is not None:
                from ddtrace.appsec._constants import PRODUCTS

                if pubsub_instance is None:
                    pubsub_instance = (
                        remoteconfig_poller.get_registered(PRODUCTS.ASM_FEATURES)
                        or remoteconfig_poller.get_registered(PRODUCTS.ASM)
                        or AppSecRC(_preprocess_results_appsec_1click_activation, _appsec_callback)
                    )

                if rc_asm_enabled and _appsec_rc_file_is_not_static():
                    remoteconfig_poller.register(PRODUCTS.ASM_DATA, pubsub_instance)  # IP Blocking
                    remoteconfig_poller.register(PRODUCTS.ASM, pubsub_instance)  # Exclusion Filters & Custom Rules
                    remoteconfig_poller.register(PRODUCTS.ASM_DD, pubsub_instance)  # DD Rules
                else:
                    remoteconfig_poller.unregister(PRODUCTS.ASM_DATA)
                    remoteconfig_poller.unregister(PRODUCTS.ASM)
                    remoteconfig_poller.unregister(PRODUCTS.ASM_DD)

            features["asm"] = {"enabled": rc_asm_enabled}
    return features


def _appsec_1click_activation(features: Mapping[str, Any], test_tracer: Optional[Tracer] = None) -> None:
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
    if _appsec_rc_features_is_enabled():
        # Tracer is a parameter for testing propose
        # Import tracer here to avoid a circular import
        if test_tracer is None:
            from ddtrace import tracer
        else:
            tracer = test_tracer

        log.debug("[%s][P: %s] ASM_FEATURES: %s", os.getpid(), os.getppid(), str(features)[:100])
        if APPSEC_ENV in os.environ:
            # no one click activation if var env is set
            rc_asm_enabled = asbool(os.environ.get(APPSEC_ENV))
        elif features is False:
            rc_asm_enabled = False
        else:
            rc_asm_enabled = features.get("asm", {}).get("enabled", False)

        log.debug("APPSEC_ENABLED: %s", rc_asm_enabled)
        if rc_asm_enabled is not None:
            log.debug(
                "[%s][P: %s] Updating ASM Remote Configuration ASM_FEATURES: %s",
                os.getpid(),
                os.getppid(),
                rc_asm_enabled,
            )

            if rc_asm_enabled:
                if not tracer._asm_enabled:
                    tracer.configure(appsec_enabled=True)
                else:
                    asm_config._asm_enabled = True
            else:
                if tracer._asm_enabled:
                    tracer.configure(appsec_enabled=False)
                else:
                    asm_config._asm_enabled = False


def _appsec_api_security_settings(features: Mapping[str, Any], test_tracer: Optional[Tracer] = None) -> None:
    """
    Deprecated
    Update API Security settings from remote config
    Actually: Update sample rate
    """
    if config._remote_config_enabled and asm_config._api_security_enabled:
        rc_api_security_sample_rate = features.get("api_security", {}).get("request_sample_rate", None)
        if rc_api_security_sample_rate is not None:
            try:
                sample_rate = max(0.0, min(1.0, float(rc_api_security_sample_rate)))
                asm_config._api_security_sample_rate = sample_rate
            except Exception:  # nosec
                pass
