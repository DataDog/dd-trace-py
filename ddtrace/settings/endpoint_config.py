"""
This module contains the logic to configure ddtrace products from a configuration endpoint.
The configuration endpoint is a URL that returns a JSON object with the configuration for the products.
It takes precedence over environment variables and configuration files.
"""
import json
import os
from urllib import parse

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import connector


log = get_logger(__name__)


def fetch_config_from_endpoint() -> dict:
    """
    Fetch the configuration from the configuration endpoint.
    """
    config_endpoint = os.getenv("DD_CONFIG_ENDPOINT", None)

    if config_endpoint is None:
        log.debug("Configuration endpoint not set. Skipping fetching configuration.")
        return {}

    try:
        parsed_url = parse.urlparse(config_endpoint)
        connect = connector(config_endpoint)
        with connect() as conn:
            conn.request("GET", parsed_url.path or "/")
            response = conn.getresponse()
            if not (200 <= response.status < 300):
                log.error("Failed to fetch configuration from endpoint: %s", response.read())
                return {}

            data = response.read().decode("utf-8")
            log.debug("Fetched configuration from endpoint: %s", data)
            return json.loads(data)

    except (json.JSONDecodeError, Exception) as exc:
        log.error("Failed to fetch configuration from endpoint: %s", exc)

    return {}
