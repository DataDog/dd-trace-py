import base64
import os
from typing import Optional

import ddtrace
from ddtrace.appsec._utils import _appsec_rc_features_is_enabled


def _appsec_rc_file_is_not_static():
    return "DD_APPSEC_RULES" not in os.environ


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
    tracer = ddtrace.tracer if test_tracer is None else test_tracer
    value = 0b0
    result = ""
    if ddtrace.config._remote_config_enabled:
        if _appsec_rc_features_is_enabled():
            value |= 1 << 1  # Enable ASM_ACTIVATION
        if tracer._appsec_processor and _appsec_rc_file_is_not_static():
            value |= 1 << 2  # Enable ASM_IP_BLOCKING
            value |= 1 << 3  # Enable ASM_DD_RULES
            value |= 1 << 4  # Enable ASM_EXCLUSIONS
            value |= 1 << 5  # Enable ASM_REQUEST_BLOCKING
            value |= 1 << 6  # Enable ASM_ASM_RESPONSE_BLOCKING
            value |= 1 << 7  # Enable ASM_USER_BLOCKING
            value |= 1 << 8  # Enable ASM_CUSTOM_RULES
            value |= 1 << 9  # Enable ASM_CUSTOM_BLOCKING_RESPONSE
            value |= 1 << 10  # Enable ASM_TRUSTED_IPS
        result = base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).decode()
    return result
