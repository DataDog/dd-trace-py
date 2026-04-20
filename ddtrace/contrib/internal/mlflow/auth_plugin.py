"""
MLFlow authentication plugin for Datadog API key injection.
"""

from ddtrace.internal.settings._config import config


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
    as 'DD-API-KEY' and 'DD-APPLICATION-KEY' headers into all MLFlow HTTP requests,
    as long as the DD_MODEL_LAB_ENABLED environment variable is set.
    """

    def in_context(self):
        """
        Check if the Datadog API and application keys are configured.

        Returns:
            bool: True if DD_API_KEY, DD_APP_KEY and DD_MODEL_LAB_ENABLED env variables are set, else False
        """
        return config._dd_api_key and config._dd_app_key and config._model_lab_enabled

    def request_headers(self):
        """
        Generate request headers with the Datadog API and application keys.

        Returns:
            dict: Dictionary containing the DD-API-KEY and DD-APPLICATION-KEY headers
                  if DD_API_KEY, DD_APP_KEY and DD_MODEL_LAB_ENABLED are set, empty dictionary otherwise.
        """
        if config._dd_api_key and config._dd_app_key and config._model_lab_enabled:
            return {"DD-API-KEY": config._dd_api_key, "DD-APPLICATION-KEY": config._dd_app_key}
        return {}
