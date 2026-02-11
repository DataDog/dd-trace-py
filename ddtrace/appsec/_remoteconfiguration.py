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
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.trace import tracer


log = get_logger(__name__)

APPSEC_PRODUCTS = {PRODUCTS.ASM_FEATURES, PRODUCTS.ASM, PRODUCTS.ASM_DATA, PRODUCTS.ASM_DD}


def enable_appsec_rc() -> None:
    """Remote config will be used by ASM libraries to receive four different updates from the backend.
    Each update has it's own product:
    - ASM_FEATURES product - To allow users enable or disable ASM remotely
    - ASM product - To allow clients to activate or deactivate rules
    - ASM_DD product - To allow the library to receive rules updates
    - ASM_DATA product - To allow the library to receive list of blocked IPs and users

    If environment variable `DD_APPSEC_ENABLED` is not set, registering ASM_FEATURE can enable ASM remotely.
    If it's set to true, we will register the rest of the products.
    """
    log.debug("[%s][P: %s] Register ASM Remote Config Callback", os.getpid(), os.getppid())

    if _asm_feature_is_required():
        remoteconfig_poller.register(
            PRODUCTS.ASM_FEATURES,
            _appsec_callback,
            capabilities=[_rc_capabilities()],
        )

    # Register other ASM products if AppSec is enabled
    if asm_config._asm_enabled and asm_config._asm_static_rule_file is None:
        remoteconfig_poller.register(PRODUCTS.ASM_DATA, _appsec_callback)  # IP Blocking
        remoteconfig_poller.register(PRODUCTS.ASM, _appsec_callback)  # Exclusion Filters & Custom Rules
        remoteconfig_poller.register(PRODUCTS.ASM_DD, _appsec_callback)  # DD Rules

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


class AppSecCallback(RCCallback):
    """Remote config callback for AppSec products."""

    def __init__(self) -> None:
        """Initialize the AppSec callback."""
        self._cache: Dict[str, Dict[str, Any]] = {}

    def __call__(self, payloads: Sequence[Payload]) -> None:
        """Process AppSec configuration payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
        if not payloads:
            return
        result = _update_asm_features(payloads, self._cache)
        if "asm" in result:
            if asm_config._asm_static_rule_file is None:
                if result["asm"].get("enabled", False):
                    # Register additional ASM products with the same callback
                    remoteconfig_poller.register(PRODUCTS.ASM_DATA, self)  # IP Blocking
                    remoteconfig_poller.register(PRODUCTS.ASM, self)  # Exclusion Filters & Custom Rules
                    remoteconfig_poller.register(PRODUCTS.ASM_DD, self)  # DD Rules
                else:
                    remoteconfig_poller.unregister(PRODUCTS.ASM_DATA)
                    remoteconfig_poller.unregister(PRODUCTS.ASM)
                    remoteconfig_poller.unregister(PRODUCTS.ASM_DD)
        debug_info = (
            f"appsec._remoteconfiguration.deb::_appsec_callback::payload"
            f"{tuple(p.path for p in payloads)}[{os.getpid()}][P: {os.getppid()}]"
        )
        log.debug(debug_info)

        for_the_waf_updates: List[tuple[str, str, PayloadType]] = []
        for_the_waf_removals: List[tuple[str, str]] = []
        for_the_tracer: List[Payload] = []
        for payload in payloads:
            if payload.metadata.product_name == "ASM_FEATURES":
                for_the_tracer.append(payload)
            elif payload.content is None:
                for_the_waf_removals.append((payload.metadata.product_name, payload.path))
            else:
                for_the_waf_updates.append((payload.metadata.product_name, payload.path, payload.content))
        _process_asm_features(for_the_tracer)
        if (for_the_waf_removals or for_the_waf_updates) and asm_config._asm_enabled:
            core.dispatch("waf.update", (for_the_waf_removals, for_the_waf_updates))


# Create singleton instance for global usage
_appsec_callback = AppSecCallback()


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


def _process_asm_features(payload_list: List[Payload], cache: Dict[str, Dict[str, Any]] = {}):
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
            enable_asm()
        else:
            disable_asm()
    if "auto_user_instrum" in result:
        asm_config._auto_user_instrumentation_rc_mode = result["auto_user_instrum"].get("mode", None)


def disable_asm():
    if asm_config._asm_enabled:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        AppSecSpanProcessor.disable()

        asm_config._asm_enabled = False
        if asm_config._api_security_active:
            from ddtrace.appsec._api_security.api_manager import APIManager

            APIManager.disable()

        tracer.configure(appsec_enabled=False)


def enable_asm():
    if asm_config._asm_can_be_enabled and not asm_config._asm_enabled:
        from ddtrace.appsec._listeners import load_appsec

        asm_config._asm_enabled = True
        if asm_config._api_security_enabled:
            from ddtrace.appsec._api_security.api_manager import APIManager

            APIManager.enable()
        load_appsec()
        tracer.configure(appsec_enabled=True, appsec_enabled_origin=APPSEC.ENABLED_ORIGIN_RC)
