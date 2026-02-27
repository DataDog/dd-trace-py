"""
MLFlow authentication plugin for Datadog API key injection.
"""

import os


try:
    from mlflow.tracking.request_header.abstract_request_header_provider import RequestHeaderProvider
except ImportError:
    # If MLFlow is not installed, create a dummy base class so the module can still be imported
    class RequestHeaderProvider:  # type: ignore[no-redef]
        pass


class DatadogHeaderProvider(RequestHeaderProvider):
    """
    MLFlow request header provider that injects Datadog API keys.

    This plugin reads the DD_API_KEY and DD_APP_KEY environment variables and injects them
    as a 'DD-API-KEY' and 'DD-APPLICATION-KEY' headers into all MLFlow HTTP requests.
    """

    def in_context(self):
        """
        Check if the Datadog API and application keys are configured.

        Returns:
            bool: True if DD_API_KEY and DD_APP_KEY environment variables are set, False otherwise.
        """
        return "DD_API_KEY" in os.environ and "DD_APP_KEY" in os.environ

    def request_headers(self):
        """
        Generate request headers with the Datadog API and application keys.

        Returns:
            dict: Dictionary containing the DD-API-KEY and DD-APPLICATION-KEY headers
                  if DD_API_KEY and DD_APP_KEY are set, empty dictionary otherwise.
        """
        api_key = os.environ.get("DD_API_KEY")
        app_key = os.environ.get("DD_APP_KEY")
        if api_key and app_key:
            return {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key}
        return {}
