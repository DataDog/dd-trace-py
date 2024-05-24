import base64
import enum
import os
from typing import Optional

import ddtrace
from ddtrace.appsec._utils import _appsec_rc_features_is_enabled
from ddtrace.settings.asm import config as asm_config


def _appsec_rc_file_is_not_static():
    return "DD_APPSEC_RULES" not in os.environ


def _asm_feature_is_required():
    flags = _rc_capabilities()
    return Flags.ASM_ACTIVATION in flags or Flags.ASM_API_SECURITY_SAMPLE_RATE in flags


class Flags(enum.IntFlag):
    ASM_ACTIVATION = 1 << 1
    ASM_IP_BLOCKING = 1 << 2
    ASM_DD_RULES = 1 << 3
    ASM_EXCLUSIONS = 1 << 4
    ASM_REQUEST_BLOCKING = 1 << 5
    ASM_ASM_RESPONSE_BLOCKING = 1 << 6
    ASM_USER_BLOCKING = 1 << 7
    ASM_CUSTOM_RULES = 1 << 8
    ASM_CUSTOM_BLOCKING_RESPONSE = 1 << 9
    ASM_TRUSTED_IPS = 1 << 10
    ASM_API_SECURITY_SAMPLE_RATE = 1 << 11
    ASM_RASP_SQLI = 1 << 21
    ASM_RASP_LFI = 1 << 22
    ASM_RASP_SSRF = 1 << 23
    ASM_RASP_SHI = 1 << 24
    ASM_RASP_XXE = 1 << 25
    ASM_RASP_RCE = 1 << 26
    ASM_RASP_NOSQLI = 1 << 27
    ASM_RASP_XSS = 1 << 28


_ALL_ASM_BLOCKING = (
    Flags.ASM_IP_BLOCKING
    | Flags.ASM_DD_RULES
    | Flags.ASM_EXCLUSIONS
    | Flags.ASM_REQUEST_BLOCKING
    | Flags.ASM_ASM_RESPONSE_BLOCKING
    | Flags.ASM_USER_BLOCKING
    | Flags.ASM_CUSTOM_RULES
    | Flags.ASM_CUSTOM_RULES
    | Flags.ASM_CUSTOM_BLOCKING_RESPONSE
)

_ALL_RASP = Flags.ASM_RASP_LFI | Flags.ASM_RASP_SSRF


def _rc_capabilities(test_tracer: Optional[ddtrace.Tracer] = None) -> Flags:
    tracer = ddtrace.tracer if test_tracer is None else test_tracer
    value = Flags(0)
    if ddtrace.config._remote_config_enabled:
        if _appsec_rc_features_is_enabled():
            value |= Flags.ASM_ACTIVATION
        if tracer._appsec_processor and _appsec_rc_file_is_not_static():
            value |= _ALL_ASM_BLOCKING
            if asm_config._ep_enabled:
                value |= _ALL_RASP
        if asm_config._api_security_enabled:
            value |= Flags.ASM_API_SECURITY_SAMPLE_RATE
    return value


def _appsec_rc_capabilities(test_tracer: Optional[ddtrace.Tracer] = None) -> str:
    r"""return the bit representation of the composed capabilities in base64
    bit 0: Reserved
    bit 1: ASM 1-click Activation
    bit 2: ASM Ip blocking

    Int Number  -> binary number    -> bytes representation -> base64 representation
    ASM Activation:
    2           -> 10               -> b'\x02'              -> "Ag=="
    ASM Ip blocking:
    4           -> 100              -> b'\x04'              -> "BA=="
    ASM Activation and ASM Ip blocking:
    6           -> 110              -> b'\x06'              -> "Bg=="
    ...
    256         -> 100000000        -> b'\x01\x00'          -> b'AQA='
    """
    value = _rc_capabilities(test_tracer=test_tracer)
    return base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode()
