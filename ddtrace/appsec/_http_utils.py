import base64
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import MutableMapping
from http.cookies import SimpleCookie
import io
import json
from typing import Any
from typing import Optional
from typing import Union
from urllib.parse import parse_qs

from ddtrace.appsec._ddwaf.ddwaf_types import DDWafInputType
from ddtrace.internal.utils import http as http_utils
from ddtrace.internal.utils.http import MediaType
from ddtrace.internal.utils.http import classify_media_type
import ddtrace.vendor.xmltodict as xmltodict


def normalize_headers(
    raw_headers: Mapping[str, object],
) -> dict[str, str]:
    """Normalize headers according to the WAF expectations.
    The WAF expects headers to be lowercased.
    """
    headers: dict[str, str] = {}
    for key, value in raw_headers.items():
        normalized_key = http_utils.normalize_header_name(key)
        if normalized_key is not None:
            headers[normalized_key] = str(value).strip()
    return headers


def parse_http_body(
    normalized_headers: dict[str, str],
    body: Optional[str],
    is_body_base64: bool,
) -> DDWafInputType:
    """Parse a request body based on the content-type header."""
    if body is None:
        return None
    if is_body_base64:
        try:
            body = base64.b64decode(body).decode()
        except (ValueError, TypeError):
            return None

    try:
        category = classify_media_type(normalized_headers.get("content-type"))
        if category is MediaType.JSON:
            return json.loads(body)  # type: ignore[no-any-return]
        elif category is MediaType.FORM_URLENCODED:
            return parse_qs(body)
        elif category is MediaType.XML:
            return xmltodict.parse(body)  # type: ignore[no-any-return]
        elif category is MediaType.MULTIPART:
            return http_utils.parse_form_multipart(body, normalized_headers)
        else:
            return None

    except Exception:
        return None


def extract_cookies_from_headers(
    normalized_headers: MutableMapping[str, str],
) -> Optional[dict[str, str]]:
    """Extract cookies from the WAF headers."""
    cookie_names = {"cookie", "set-cookie"}
    for name in cookie_names:
        if name in normalized_headers:
            cookie = SimpleCookie()
            header = normalized_headers[name]
            del normalized_headers[name]
            if header:
                cookie.load(header)
            return {k: v.value for k, v in cookie.items()}
    return None


def build_headers(lst: Iterable[tuple[str, str]]) -> dict[str, Union[str, list[str]]]:
    res: dict[str, Union[str, list[str]]] = {}
    for a, b in lst:
        if a in res:
            v = res[a]
            if isinstance(v, str):
                res[a] = [v, b]
            else:
                v.append(b)
        else:
            res[a] = b
    return res


def parse_http_response_body(response: Any) -> Any:
    """Decode a JSON http.client response body, then put it back so the application can still read it."""
    try:
        if response.length and response.headers.get("content-type", None) == "application/json":
            length = response.length
            body = response.read()
            response.fp = io.BytesIO(body)
            response.length = length
            return json.loads(body)
    except Exception:
        return None
    return None
