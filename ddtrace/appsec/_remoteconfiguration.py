# -*- coding: utf-8 -*-
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence

from ddtrace.appsec._capabilities import _asm_feature_is_required
from ddtrace.appsec._capabilities import _rc_capabilities
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import PRODUCTS
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Tracer
from ddtrace.trace import tracer


log = get_logger(__name__)

APPSEC_PRODUCTS = {PRODUCTS.ASM_FEATURES, PRODUCTS.ASM, PRODUCTS.ASM_DATA, PRODUCTS.ASM_DD}


class AppSecRC(PubSub):
    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisher
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, callback):
        self._publisher = self.__publisher_class__(
            self.__shared_data__, preprocess_func=_preprocess_results_appsec_1click_activation
        )
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

    If environment variable `DD_APPSEC_ENABLED` is not set, registering ASM_FEATURE can enable ASM remotely.
    If it's set to true, we will register the rest of the products.

    Parameters `test_tracer` and `start_subscribers` are needed for testing purposes
    """
    log.debug("[%s][P: %s] Register ASM Remote Config Callback", os.getpid(), os.getppid())
    asm_callback = (
        remoteconfig_poller.get_registered(PRODUCTS.ASM_FEATURES)
        or remoteconfig_poller.get_registered(PRODUCTS.ASM)
        or AppSecRC(_appsec_callback)
    )

    if _asm_feature_is_required():
        remoteconfig_poller.register(PRODUCTS.ASM_FEATURES, asm_callback, capabilities=[_rc_capabilities()])

    if asm_config._asm_enabled and asm_config._asm_static_rule_file is None:
        remoteconfig_poller.register(PRODUCTS.ASM_DATA, asm_callback)  # IP Blocking
        remoteconfig_poller.register(PRODUCTS.ASM, asm_callback)  # Exclusion Filters & Custom Rules
        remoteconfig_poller.register(PRODUCTS.ASM_DD, asm_callback)  # DD Rules
    # ensure exploit prevention patches are loaded by one-click activation
    if asm_config._asm_enabled:
        from ddtrace.appsec._listeners import load_common_appsec_modules

        load_common_appsec_modules()

    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
    asm_config._rc_client_id = remoteconfig_poller._client.id


def disable_appsec_rc():
    # only used to avoid data leaks between tests
    for product_name in APPSEC_PRODUCTS:
        remoteconfig_poller.unregister(product_name)

    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)


def _appsec_callback(payload_list: Sequence[Payload], test_tracer: Optional[Tracer] = None) -> None:
    if not payload_list:
        return
    debug_info = (
        f"appsec._remoteconfiguration.deb::_appsec_callback::payload"
        f"{tuple(p.path for p in payload_list)}[{os.getpid()}][P: {os.getppid()}]"
    )
    log.debug(debug_info)

    local_tracer = test_tracer or tracer

    for_the_waf_updates: List[tuple[str, str, PayloadType]] = []
    for_the_waf_removals: List[tuple[str, str]] = []
    for_the_tracer: List[Payload] = []
    for payload in payload_list:
        if payload.metadata.product_name == "ASM_FEATURES":
            for_the_tracer.append(payload)
        elif payload.content is None:
            for_the_waf_removals.append((payload.metadata.product_name, payload.path))
        else:
            for_the_waf_updates.append((payload.metadata.product_name, payload.path, payload.content))
    _process_asm_features(for_the_tracer, local_tracer)
    if (for_the_waf_removals or for_the_waf_updates) and asm_config._asm_enabled:
        core.dispatch("waf.update", (for_the_waf_removals, for_the_waf_updates))


def _update_asm_features(payload_list: Sequence[Payload], cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    res: Dict[str, Dict[str, Optional[bool]]] = {}
    for payload in payload_list:
        if payload.metadata.product_name == "ASM_FEATURES":
            payload_content = payload.content
            if payload_content is None:
                if payload.path in cache:
                    if "asm" in cache[payload.path]:
                        res["asm"] = {"enabled": False}
                    elif "auto_user_instrum" in cache[payload.path]:
                        res["auto_user_instrum"] = {"mode": None}
                cache.pop(payload.path, None)
            else:
                res.update(payload_content)
                cache[payload.path] = payload_content
    return res


def _process_asm_features(payload_list: List[Payload], local_tracer: Tracer, cache: Dict[str, Dict[str, Any]] = {}):
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
    result = _update_asm_features(payload_list, cache)
    if "asm" in result and asm_config._asm_can_be_enabled:
        if result["asm"].get("enabled", False):
            enable_asm(local_tracer)
        else:
            disable_asm(local_tracer)
    if "auto_user_instrum" in result:
        asm_config._auto_user_instrumentation_rc_mode = result["auto_user_instrum"].get("mode", None)


def disable_asm(local_tracer: Tracer):
    if asm_config._asm_enabled:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        AppSecSpanProcessor.disable()

        asm_config._asm_enabled = False
        local_tracer.configure(appsec_enabled=False)


def enable_asm(local_tracer: Tracer):
    if not asm_config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        asm_config._asm_enabled = True
        load_appsec()
        local_tracer.configure(appsec_enabled=True, appsec_enabled_origin=APPSEC.ENABLED_ORIGIN_RC)


def _preprocess_results_appsec_1click_activation(
    payload_list: List[Payload], pubsub_instance: PubSub, cache: Dict[str, Dict[str, Any]] = {}
) -> List[Payload]:
    """The main process has the responsibility to enable or disable the ASM products. The child processes don't
    care about that, the children only need to know about payload content.
    """
    result = _update_asm_features(payload_list, cache)
    if "asm" in result:
        if asm_config._asm_static_rule_file is None:
            if result["asm"].get("enabled", False):
                remoteconfig_poller.register(PRODUCTS.ASM_DATA, pubsub_instance)  # IP Blocking
                remoteconfig_poller.register(PRODUCTS.ASM, pubsub_instance)  # Exclusion Filters & Custom Rules
                remoteconfig_poller.register(PRODUCTS.ASM_DD, pubsub_instance)  # DD Rules
            else:
                remoteconfig_poller.unregister(PRODUCTS.ASM_DATA)
                remoteconfig_poller.unregister(PRODUCTS.ASM)
                remoteconfig_poller.unregister(PRODUCTS.ASM_DD)
    return payload_list
