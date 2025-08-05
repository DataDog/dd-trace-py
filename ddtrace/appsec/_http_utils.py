import base64
from http.cookies import SimpleCookie
import json
from typing import Any
from typing import Dict
from typing import Optional
from typing import Union
from urllib.parse import parse_qs

from ddtrace.internal.utils import http as http_utils
import ddtrace.vendor.xmltodict as xmltodict


def normalize_headers(
    request_headers: Dict[str, str],
) -> Dict[str, Optional[str]]:
    """Normalize headers according to the WAF expectations.

    The WAF expects headers to be lowercased and empty values to be None.
    """
    headers: Dict[str, Optional[str]] = {}
    for key, value in request_headers.items():
        normalized_key = http_utils.normalize_header_name(key)
        if value:
            headers[normalized_key] = str(value).strip()
        else:
            headers[normalized_key] = None
    return headers


def parse_http_body(
    normalized_headers: Dict[str, Optional[str]],
    body: Optional[str],
    is_body_base64: bool,
) -> Union[str, Dict[str, Any], None]:
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

        if content_type in ("application/json", "application/vnd.api+json", "text/json"):
            return json.loads(body)
        elif content_type in ("application/x-url-encoded", "application/x-www-form-urlencoded"):
            return parse_qs(body)
        elif content_type in ("application/xml", "text/xml"):
            return xmltodict.parse(body)
        elif content_type.startswith("multipart/form-data"):
            return http_utils.parse_form_multipart(body, normalized_headers)
        elif content_type == "text/plain":
            return None
        else:
            return None

    except Exception:
        return None


def extract_cookies_from_headers(
    normalized_headers: Dict[str, Optional[str]],
) -> Optional[Dict[str, str]]:
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
