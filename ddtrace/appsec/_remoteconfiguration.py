# -*- coding: utf-8 -*-
import os
from typing import Callable
from typing import Optional
from typing import Sequence
from typing import TypedDict
from typing import cast

from ddtrace.appsec._capabilities import _ALL_ASM_CAPABILITIES
from ddtrace.appsec._capabilities import _asm_feature_is_required
from ddtrace.appsec._capabilities import _rc_capabilities
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT


log = get_logger(__name__)

APPSEC_PRODUCTS = {
    RemoteConfigProduct.AsmFeatures,
    RemoteConfigProduct.Asm,
    RemoteConfigProduct.AsmData,
    RemoteConfigProduct.AsmDd,
}


class _AsmFeatureState(TypedDict, total=False):
    enabled: bool


class _AutoUserInstrumState(TypedDict, total=False):
    mode: Optional[str]


class AsmFeaturesResult(TypedDict, total=False):
    """Shape of an ASM_FEATURES RC payload's content, and of the dict _update_asm_features
    derives from a batch of such payloads.
    """

    asm: _AsmFeatureState
    auto_user_instrum: _AutoUserInstrumState


def enable_appsec_rc(callback: "AppSecCallback") -> None:
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
        remoteconfig_poller.register_callback(
            RemoteConfigProduct.AsmFeatures,
            callback,
            capabilities=_rc_capabilities(),
        )
        remoteconfig_poller.enable_product(RemoteConfigProduct.AsmFeatures)

    # Register other ASM products if AppSec is enabled
    if asm_config._asm_enabled and asm_config._asm_static_rule_file is None:
        remoteconfig_poller.register_callback(RemoteConfigProduct.AsmData, callback)  # IP Blocking
        remoteconfig_poller.enable_product(RemoteConfigProduct.AsmData)
        remoteconfig_poller.register_callback(RemoteConfigProduct.Asm, callback)  # Exclusion Filters & Custom Rules
        remoteconfig_poller.enable_product(RemoteConfigProduct.Asm)
        remoteconfig_poller.register_callback(RemoteConfigProduct.AsmDd, callback)  # DD Rules
        remoteconfig_poller.enable_product(RemoteConfigProduct.AsmDd)

    if asm_config._asm_enabled:
        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
    asm_config._rc_client_id = remoteconfig_poller._client.id


def disable_appsec_rc() -> None:
    for product_name in APPSEC_PRODUCTS:
        remoteconfig_poller.unregister_callback(product_name)
        remoteconfig_poller.disable_product(product_name)


class AppSecCallback(RCCallback):
    """Remote config callback for AppSec products."""

    def __init__(self, enable_asm: Callable[[], None], disable_asm: Callable[[], None]) -> None:
        """Initialize the AppSec callback."""
        self._cache: dict[str, AsmFeaturesResult] = {}
        self._enable_asm = enable_asm
        self._disable_asm = disable_asm

    def __call__(self, payloads: Sequence[Payload]) -> None:
        """Process AppSec configuration payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
        if not payloads:
            return
        # ASM_FEATURES payloads are folded into `result` here, once, and reused below for both
        # the immediate product (de)registration and the enable_asm/disable_asm decision. Calling
        # _update_asm_features a second time on the same payloads against the same cache would
        # consume the pop-on-removal transition it just recorded and silently drop the second
        # decision (e.g. missing a disable_asm() call on an ASM_FEATURES removal payload).
        result = _update_asm_features(payloads, self._cache)
        if "asm" in result:
            if asm_config._asm_static_rule_file is None:
                if result["asm"].get("enabled", False):
                    # Register additional ASM products with the same callback
                    remoteconfig_poller.register_callback(RemoteConfigProduct.AsmData, self)  # IP Blocking
                    remoteconfig_poller.enable_product(RemoteConfigProduct.AsmData)
                    remoteconfig_poller.register_callback(
                        RemoteConfigProduct.Asm, self
                    )  # Exclusion Filters & Custom Rules
                    remoteconfig_poller.enable_product(RemoteConfigProduct.Asm)
                    remoteconfig_poller.register_callback(RemoteConfigProduct.AsmDd, self)  # DD Rules
                    remoteconfig_poller.enable_product(RemoteConfigProduct.AsmDd)
                else:
                    remoteconfig_poller.unregister_callback(RemoteConfigProduct.AsmData)
                    remoteconfig_poller.disable_product(RemoteConfigProduct.AsmData)
                    remoteconfig_poller.unregister_callback(RemoteConfigProduct.Asm)
                    remoteconfig_poller.disable_product(RemoteConfigProduct.Asm)
                    remoteconfig_poller.unregister_callback(RemoteConfigProduct.AsmDd)
                    remoteconfig_poller.disable_product(RemoteConfigProduct.AsmDd)
        debug_info = (
            f"appsec._remoteconfiguration.deb::AppSecCallback::payload"
            f"{tuple(p.path for p in payloads)}[{os.getpid()}][P: {os.getppid()}]"
        )
        log.debug(debug_info)

        for_the_waf_updates: list[tuple[str, str, PayloadType]] = []
        for_the_waf_removals: list[tuple[str, str]] = []
        for payload in payloads:
            if payload.metadata.product_name == "ASM_FEATURES":
                continue  # already folded into `result` above
            elif payload.content is None:
                for_the_waf_removals.append((payload.metadata.product_name, payload.path))
            else:
                for_the_waf_updates.append((payload.metadata.product_name, payload.path, payload.content))
        _process_asm_features(result, enable_asm=self._enable_asm, disable_asm=self._disable_asm)
        if (for_the_waf_removals or for_the_waf_updates) and asm_config._asm_enabled:
            core.dispatch("waf.update", (for_the_waf_removals, for_the_waf_updates))


def _update_asm_features(payload_list: Sequence[Payload], cache: dict[str, AsmFeaturesResult]) -> AsmFeaturesResult:
    res: AsmFeaturesResult = {}
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
                asm_features_content = cast(AsmFeaturesResult, payload_content)
                res.update(asm_features_content)
                cache[payload.path] = asm_features_content
    return res


def _process_asm_features(
    result: AsmFeaturesResult,
    enable_asm: Callable[[], None],
    disable_asm: Callable[[], None],
) -> None:
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

    `result` is the dict already produced by _update_asm_features for the current payload batch;
    callers must not call _update_asm_features again for the same batch (it pops the cache entry
    on a removal payload, so a second call would silently miss the enable/disable decision).
    """
    if "asm" in result and asm_config._asm_can_be_enabled:
        if result["asm"].get("enabled", False):
            enable_asm()
        else:
            disable_asm()
    if "auto_user_instrum" in result:
        asm_config._auto_user_instrumentation_rc_mode = result["auto_user_instrum"].get("mode", None)
    if "asm" in result or "auto_user_instrum" in result:
        # Re-advertise capabilities so blocking/RASP follow one-click activation/deactivation.
        remoteconfig_poller.update_capabilities(_ALL_ASM_CAPABILITIES, _rc_capabilities())
