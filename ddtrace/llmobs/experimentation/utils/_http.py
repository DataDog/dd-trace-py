import os
from typing import Dict, Optional

from ..._utils import HTTPResponse, http_request
from .._config import ENV_DD_API_KEY, ENV_DD_APPLICATION_KEY, ENV_DD_SITE, BASE_URL

def exp_http_request(method: str, url: str, body: Optional[bytes] = None) -> HTTPResponse:
    """
    Make an HTTP request to the Datadog LLM Observability/Experiments API.

    Args:
        method (str): HTTP method (e.g., "GET", "POST", "PATCH").
        url (str): The full endpoint URL (path is appended to BASE_URL).
        body (Optional[bytes]): Request body in bytes (for POST/PATCH).

    Returns:
        HTTPResponse: A wrapper object containing the response status code, headers, and body.

    Raises:
        ValueError: If the request fails with a 4xx or 5xx response from Datadog.
    """
    headers: Dict[str, str] = {
        "DD-API-KEY": os.getenv(ENV_DD_API_KEY, ""),
        "DD-APPLICATION-KEY": os.getenv(ENV_DD_APPLICATION_KEY, ""),
        "Content-Type": "application/json",
    }
    full_url = BASE_URL + url
    resp = http_request(method, full_url, headers=headers, body=body)
    if resp.status_code == 403:
        if not ENV_DD_SITE:
            raise ValueError(
                "DD_SITE may be incorrect. Please check your DD_SITE environment variable or pass it as an argument to init(...site=...)"
            )
        else:
            raise ValueError(
                "DD_API_KEY or DD_APPLICATION_KEY is incorrect. Please check your DD_API_KEY and DD_APPLICATION_KEY environment variables or pass them as arguments to init(...api_key=..., application_key=...)"
            )
    if resp.status_code >= 400:
        try:
            error_details = resp.json()
            error_message = error_details.get("errors", [{}])[0].get("detail", resp.text())
        except Exception:
            error_message = resp.text()
        raise ValueError(f"Request failed with status code {resp.status_code}: {error_message}")
    return resp

