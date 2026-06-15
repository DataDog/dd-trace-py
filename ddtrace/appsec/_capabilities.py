import base64

from ddtrace.internal.native import RemoteConfigCapabilities as Cap
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.asm import config as asm_config


# Capability definitions are owned by libdatadog and exposed via PyO3
# (RemoteConfigCapabilities); we reference them here rather than duplicating the
# bit positions.
_ALL_ASM_BLOCKING = [
    Cap.AsmIpBlocking,
    Cap.AsmDdRules,
    Cap.AsmExclusions,
    Cap.AsmRequestBlocking,
    Cap.AsmResponseBlocking,
    Cap.AsmUserBlocking,
    Cap.AsmCustomRules,
    Cap.AsmCustomBlockingResponse,
    Cap.AsmProcessorOverrides,
    Cap.AsmCustomDataScanners,
    Cap.AsmExclusionData,
    Cap.AsmEndpointFingerprint,
    Cap.AsmSessionFingerprint,
    Cap.AsmNetworkFingerprint,
    Cap.AsmHeaderFingerprint,
    Cap.AsmDdMulticonfig,
    Cap.AsmTraceTaggingRules,
]

_ALL_RASP = [Cap.AsmRaspSqli, Cap.AsmRaspLfi, Cap.AsmRaspSsrf, Cap.AsmRaspShi, Cap.AsmRaspCmdi]
_FEATURE_REQUIRED = (Cap.AsmActivation, Cap.AsmAutoUserInstrumMode)

# Mask of every bit _rc_capabilities() may set, so re-advertising replaces only ASM-owned bits.
_ALL_ASM_CAPABILITIES = Flags.ASM_ACTIVATION | Flags.ASM_AUTO_USER | _ALL_ASM_BLOCKING | _ALL_RASP


def _asm_feature_is_required() -> bool:
    caps = _rc_capabilities()
    return any(c in caps for c in _FEATURE_REQUIRED)


def _rc_capabilities() -> list:
    value: list = []
    if config._remote_config_enabled:
        if asm_config._asm_can_be_enabled:
            value.append(Cap.AsmActivation)
        if asm_config._asm_enabled and asm_config._asm_static_rule_file is None:
            value.extend(_ALL_ASM_BLOCKING)
            if asm_config._ep_enabled:
                value.extend(_ALL_RASP)
        if asm_config._auto_user_instrumentation_enabled:
            value.append(Cap.AsmAutoUserInstrumMode)
    return value


def _appsec_rc_capabilities() -> str:
    """Return the base64 of the composed capabilities bit mask.

    The capabilities sent to the agent are now encoded natively by the remote
    config client; this helper reconstructs the same base64 representation from
    the capability set for assertions/diagnostics.
    """
    value = 0
    for cap in _rc_capabilities():
        value |= 1 << int(cap)
    return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode()
