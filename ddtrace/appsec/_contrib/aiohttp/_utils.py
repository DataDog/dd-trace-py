from typing import Any
from typing import Union
from urllib.parse import parse_qsl

from ddtrace.appsec._ddwaf.ddwaf_types import DDWAF_MAX_STRING_LENGTH
from ddtrace.internal.utils.http import is_utf8_charset


# One character past the WAF limit, so the WAF still reports the value as truncated.
_MAX_STORED_LENGTH = DDWAF_MAX_STRING_LENGTH + 1


def single_or_list(fields: dict[str, list[Any]]) -> dict[str, Any]:
    return {key: values[0] if len(values) == 1 else values for key, values in fields.items()}


def decode_text(data: Union[bytes, bytearray], charset: str) -> str:
    try:
        text = data.decode(charset, errors="replace")
    except Exception:
        text = data.decode("utf-8", errors="replace")
    return text[:_MAX_STORED_LENGTH]


def parse_form(data: Union[bytes, bytearray], charset: str) -> dict[str, Any]:
    """Parse a urlencoded body with its charset, adding its UTF-8 values when the two decodings differ."""
    utf8_text = data.decode("utf-8", errors="replace").rstrip()
    try:
        text = utf8_text if is_utf8_charset(charset) else data.decode(charset, errors="replace").rstrip()
        pairs = parse_qsl(text, keep_blank_values=True, encoding=charset)
    except Exception:
        text = utf8_text
        pairs = parse_qsl(text, keep_blank_values=True)
    form: dict[str, list[str]] = {}
    for key, value in pairs:
        form.setdefault(key, []).append(value)
    if text != utf8_text:
        seen = {key: set(values) for key, values in form.items()}
        for key, value in parse_qsl(utf8_text, keep_blank_values=True):
            if value not in seen.setdefault(key, set()):
                seen[key].add(value)
                form.setdefault(key, []).append(value)
    return single_or_list(form)
