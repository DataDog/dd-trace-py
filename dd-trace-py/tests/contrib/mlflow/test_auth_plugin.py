from ddtrace.contrib.internal.mlflow.auth_plugin import DatadogHeaderProvider
from tests.utils import override_global_config


class TestDatadogHeaderProvider:
    def test_auth_plugin(self):
        test_api_key = "test_api_key_12345"
        test_app_key = "test_app_key_12345"
        with override_global_config(
            dict(
                _dd_api_key=test_api_key,
                _dd_app_key=test_app_key,
                _model_lab_enabled=True,
            )
        ):
            provider = DatadogHeaderProvider()
            assert provider.in_context()
            headers = provider.request_headers()
            assert headers == {"DD-API-KEY": test_api_key, "DD-APPLICATION-KEY": test_app_key}
