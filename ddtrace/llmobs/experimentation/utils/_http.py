import os
from typing import Dict, Optional

from ..._utils import HTTPResponse, http_request
from .._config import get_api_key, get_application_key, get_site, get_api_base_url
from ._exceptions import DatadogAPIError, DatadogAuthenticationError


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
        DatadogAuthenticationError: If authentication fails (403 status code).
        DatadogAPIError: For other 4xx or 5xx responses from Datadog.
    """
    headers: Dict[str, str] = {
        "DD-API-KEY": get_api_key(),
        "DD-APPLICATION-KEY": get_application_key(),
        "Content-Type": "application/json",
    }
    full_url = get_api_base_url() + url
    resp = http_request(method, full_url, headers=headers, body=body)
    if resp.status_code == 403:
        if not get_site():
            raise DatadogAuthenticationError(
                resp.status_code,
                "Site configuration is missing. Please check your DD_SITE environment variable or pass it as an argument to init(...site=...)",
            )
        else:
            if resp.status_code == 403:
                raise DatadogAuthenticationError(
                    resp.status_code,
                    "API key or Application key is incorrect. Also, make sure you are using the correct site (e.g. us3.datadoghq.com, us5.datadoghq.com, etc.)",
                )
            else:
                raise DatadogAPIError(resp.status_code, resp.text())
    if resp.status_code >= 400:
        try:
            error_details = resp.json()
            error_message = error_details.get("errors", [{}])[0].get("detail", resp.text())
        except Exception:
            error_message = resp.text()
        raise DatadogAPIError(resp.status_code, error_message)
    return resp
