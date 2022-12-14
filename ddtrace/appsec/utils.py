import base64
import os
import sys
from typing import TYPE_CHECKING

from ddtrace.constants import APPSEC_ENV
from ddtrace import constants
from ddtrace.internal.utils.formats import asbool


if TYPE_CHECKING:  # pragma: no cover
    from typing import Text


def _appsec_rc_features_is_enabled():
    # type: () -> bool
    if asbool(os.environ.get("DD_REMOTE_CONFIGURATION_ENABLED", "true")):
        return APPSEC_ENV not in os.environ
    return False


def to_bytes_py2(n, length=1, byteorder="big", signed=False):
    # type: (int, int, str, bool) -> Text
    if byteorder == "little":
        order = range(length)
    elif byteorder == "big":
        order = reversed(range(length))  # type: ignore[assignment]
    else:
        raise ValueError("byteorder must be either 'little' or 'big'")

    # return bytes((n >> i*8) & 0xff for i in order)
    return "".join(chr((n >> i * 8) & 0xFF) for i in order)


def _appsec_rc_capabilities():
    # type: () -> str
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
    value = 0b0

    if _appsec_rc_features_is_enabled():
        value |= 1 << 1
        value |= 1 << 2

    result = ""
    if sys.version_info.major < 3:
        bytes_res = to_bytes_py2(value, (value.bit_length() + 7) // 8, "big")
        result = base64.b64encode(bytes_res)  # type: ignore[assignment, arg-type]
    else:
        result = str(base64.b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")), encoding="utf-8")

    return result


def _get_blocked_template(accept_header_value):
    # type: (str) -> str

    need_html_template = False

    if accept_header_value and 'text/html' in accept_header_value.lower():
        need_html_template = True

    if need_html_template:
        template_path = os.getenv('DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML')
    else:
        template_path = os.getenv('DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON')

    if template_path and os.path.exists(template_path) \
            and os.path.isfile(template_path):
        try:
            with open(template_path, 'r') as template_file:
                return template_file.read()
        except OSError:
            pass

    # No user-defined template at this point
    if need_html_template:
        return constants.APPSEC_BLOCKED_RESPONSE_HTML

    return constants.APPSEC_BLOCKED_RESPONSE_JSON
