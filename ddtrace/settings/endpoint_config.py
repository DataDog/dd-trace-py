"""
This module contains the logic to configure ddtrace products from a configuration endpoint.
The configuration endpoint is a URL that returns a JSON object with the configuration for the products.
It takes precedence over environment variables and configuration files.
"""
import json
import os

import urllib3

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


def fetch_config_from_endpoint() -> dict:
    # return {"iast_enabled": True}
    """
    Fetch the configuration from the configuration endpoint.
    """
    config_endpoint = os.getenv("DD_CONFIG_ENDPOINT", None)

    if config_endpoint is None:
        log.debug("Configuration endpoint not set. Skipping fetching configuration.")
        return {}

    try:
        http = urllib3.PoolManager()
        response = http.request("GET", config_endpoint)
        log.debug("Fetched configuration from endpoint: %s", response.data)
        return json.loads(response.data)

    except (urllib3.exceptions.HTTPError, json.JSONDecodeError, Exception) as exc:
        log.error("Failed to fetch configuration from endpoint: %s", exc)

    return {}
