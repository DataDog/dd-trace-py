import multiprocessing
import os
from ctypes import c_char
from typing import TYPE_CHECKING

from ddtrace import config
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.appsec.utils import _appsec_rc_features_is_enabled
from ddtrace.constants import APPSEC_ENV
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.v2.connectors import ConnectorFile, ConnectorSharedMemory
from ddtrace.internal.remoteconfig.v2.pubsub import PubSubBase, PubSubMergeMergeFirst
from ddtrace.internal.remoteconfig.v2.subscribers import RemoteConfigSubscriber
from ddtrace.internal.utils.formats import asbool


try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Dict

    try:
        from typing import Literal
    except ImportError:
        # Python < 3.8. The "type ignore" is to avoid a runtime check just to silence mypy.
        from typing_extensions import Literal  # type: ignore
    from typing import Mapping
    from typing import Optional
    from typing import Union

    from ddtrace import Tracer

log = get_logger(__name__)


class AppSecRC(PubSubMergeMergeFirst):
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data = ConnectorFile()
    # __shared_data = ConnectorSharedMemory()

    def __init__(self,  _preprocess_results, callback, name="Default"):
        log.debug("[%s][P: %s] PublisherListenerProxy created!!!", os.getpid(), os.getppid())
        self._publisher = self.__publisher_class__(self.__shared_data, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data, callback, name)


def enable_appsec_rc(test_tracer=None):
    # type: (Optional[Tracer]) -> None
    # Tracer is a parameter for testing propose
    # Import tracer here to avoid a circular import
    from ddtrace.internal.remoteconfig.v2.worker import remoteconfig_poller
    if test_tracer is None:
        from ddtrace import tracer
    else:
        tracer = test_tracer

    log.info("[%s] STARTING enable_appsec_rc!!!!!!!!!!!!!!!!!" % os.getpid())
    asm_callback = AppSecRC(_preprocess_results_appsec_1click_activation,
                            _appsec_callback,
                            "ASM")
    if _appsec_rc_features_is_enabled():
        remoteconfig_poller.register(PRODUCTS.ASM_FEATURES, asm_callback)

    remoteconfig_poller.register(PRODUCTS.ASM_DATA, asm_callback)  # IP Blocking
    remoteconfig_poller.register(PRODUCTS.ASM, asm_callback)  # Exclusion Filters & Custom Rules
    # remoteconfig_poller.register(PRODUCTS.ASM_DD, asm_callback)  # DD Rules
    asm_callback.start_listener()


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


def _appsec_callback(features):
    _appsec_1click_activation(features)
    _appsec_rules_data(features)


def _appsec_rules_data(features):
    # type: (Mapping[str, Any]) -> bool
    from ddtrace import tracer

    log.info("[{}] _appsec_rules_data 1 {} {}!!!!!!!!!!!!".format(os.getpid(), features, tracer._appsec_processor))
    # log.info("DDWAF {}!!".format(tracer._appsec_processor._ddwaf))
    if features and tracer._appsec_processor:
        log.info("[{}] _appsec_rules_data 2 !!!!!!!!!!!!".format(os.getpid()))
        ruleset = {
            "rules": None,
            "rules_data": None,
            "exclusions": None,
            "rules_override": None,
        }  # type: dict[str, Optional[list[Any]]]
        _add_rules_to_list(features, "rules_data", "rules data", ruleset)
        _add_rules_to_list(features, "custom_rules", "custom rules", ruleset)
        _add_rules_to_list(features, "rules", "Datadog rules", ruleset)
        _add_rules_to_list(features, "exclusions", "exclusion filters", ruleset)
        _add_rules_to_list(features, "rules_override", "rules override", ruleset)
        # log.info("rulset rules {}!!!!".format(str(ruleset["rules"])[:100]))
        return tracer._appsec_processor._update_rules({k: v for k, v in ruleset.items() if v is not None})

    return False


def _preprocess_results_appsec_1click_activation(features):
    if features is not None:
        rc_appsec_enabled = None
        if APPSEC_ENV in os.environ:
            # no one click activation if var env is set
            rc_appsec_enabled = asbool(os.environ.get(APPSEC_ENV))
        elif features is False:
            rc_appsec_enabled = False
        else:
            asm_features = features.get("asm", {})
            if asm_features is not None:
                rc_appsec_enabled = asm_features.get("enabled")

        if rc_appsec_enabled is not None:
            from ddtrace.appsec._constants import PRODUCTS
            from ddtrace.internal.remoteconfig.v2.worker import remoteconfig_poller

            log.debug(
                "[%s][P: %s] _preprocess_results_appsec_1click_activation. Updating ASM Remote Configuration ASM_FEATURES: %s",
                os.getpid(),
                os.getppid(),
                rc_appsec_enabled,
            )

            if rc_appsec_enabled:
                # asm_callback = PublisherListenerProxy(RemoteConfigPublisherAfterMerge, None, _appsec_rules_data, "ASM PRE")
                # remoteconfig_poller.register(PRODUCTS.ASM_DATA, asm_callback)  # IP Blocking
                # remoteconfig_poller.register(PRODUCTS.ASM, asm_callback)  # Exclusion Filters & Custom Rules
                # remoteconfig_poller.register(PRODUCTS.ASM_DD, asm_callback)  # DD Rules
                # asm_callback.listener.start()
                pass
            else:
                remoteconfig_poller.unregister(PRODUCTS.ASM_DATA)
                remoteconfig_poller.unregister(PRODUCTS.ASM_DATA)
                remoteconfig_poller.unregister(PRODUCTS.ASM)
                # remoteconfig_poller.unregister(PRODUCTS.ASM_DD)
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

    log.debug("[%s][P: %s] ASM_FEATURES: %s", os.getpid(), os.getppid(), features)
    if APPSEC_ENV in os.environ:
        # no one click activation if var env is set
        rc_appsec_enabled = asbool(os.environ.get(APPSEC_ENV))
    elif features is False:
        rc_appsec_enabled = False
    else:
        rc_appsec_enabled = features.get("asm", {}).get("enabled")

    log.debug("APPSEC_ENABLED: %s", rc_appsec_enabled)
    if rc_appsec_enabled is not None:

        log.debug("[%s][P: %s] Updating ASM Remote Configuration ASM_FEATURES: %s", os.getpid(), os.getppid(), rc_appsec_enabled)

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


# class RCAppSecCallBack(RemoteConfigCallBackAfterMerge):
#     def __init__(self, tracer):
#         # type: (Tracer) -> None
#         super(RCAppSecCallBack, self).__init__()
#         self.tracer = tracer
#
#     def __call__(self, target, features):
#         # type: (str, Any) -> None
#         if features is not None:
#             _appsec_rules_data(self.tracer, features)
