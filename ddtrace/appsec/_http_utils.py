import base64
from collections.abc import Mapping
from http.cookies import SimpleCookie
import json
from typing import Any
from typing import MutableMapping
from typing import Optional
from typing import Union
from urllib.parse import parse_qs

from ddtrace.internal.utils import http as http_utils
import ddtrace.vendor.xmltodict as xmltodict


def normalize_headers(
    request_headers: Mapping[str, str],
) -> dict[str, str]:
    """Normalize headers according to the WAF expectations.
    The WAF expects headers to be lowercased.
    """
    headers: dict[str, str] = {}
    for key, value in request_headers.items():
        normalized_key = http_utils.normalize_header_name(key)
        if normalized_key is not None:
            headers[normalized_key] = str(value).strip()
    return headers


def parse_http_body(
    normalized_headers: dict[str, str],
    body: Optional[str],
    is_body_base64: bool,
) -> Union[str, dict[str, Any], None]:
    """Parse a request body based on the content-type header."""
    if body is None:
        return None
    if is_body_base64:
        try:
            body = base64.b64decode(body).decode()
        except (ValueError, TypeError):
            return None

    try:
        content_type = normalized_headers.get("content-type")
        if not content_type:
            return None

        media_type = content_type.split(";", 1)[0].strip().lower()

        if media_type in ("application/json", "application/vnd.api+json", "text/json"):
            return json.loads(body)
        elif media_type in ("application/x-url-encoded", "application/x-www-form-urlencoded"):
            return parse_qs(body)
        elif media_type in ("application/xml", "text/xml"):
            return xmltodict.parse(body)
        elif media_type == "multipart/form-data":
            return http_utils.parse_form_multipart(body, normalized_headers)
        elif media_type == "text/plain":
            return None
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
