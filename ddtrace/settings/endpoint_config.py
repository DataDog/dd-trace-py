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

# This is the URL that the agent will use to fetch the configuration
# It must be set using the DD_CONFIG_ENDPOINT environment variable.
# If not set, it is not used.
CONFIG_ENDPOINT = os.getenv("DD_CONFIG_ENDPOINT", None)


# This is the fetched configuration from the configuration endpoint.
# It is a JSON object with the configuration for the products.
# It is set to empty dict by default.
ENDPOINT_RECEIVED_CONFIG = {}


def fetch_config_from_endpoint() -> dict:
    """
    Fetch the configuration from the configuration endpoint.
    """
    global ENDPOINT_RECEIVED_CONFIG
    if CONFIG_ENDPOINT is None:
        log.debug("Configuration endpoint not set. Skipping fetching configuration.")
        return {}

    try:
        http = urllib3.PoolManager()
        response = http.request("GET", CONFIG_ENDPOINT)
        ENDPOINT_RECEIVED_CONFIG = json.loads(response.data)
    except Exception as e:
        log.error("Failed to fetch configuration from endpoint: %s", e)

    return ENDPOINT_RECEIVED_CONFIG
