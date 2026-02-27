"""Tests for MLFlow authentication plugin."""

import os

import pytest


class TestDatadogHeaderProvider:
    """Test the Datadog MLFlow authentication plugin."""

    def test_auth_plugin(self, monkeypatch):
        """Test request_headers returns correct header when DD_API_KEY is set."""
        from ddtrace.contrib.internal.mlflow.auth_plugin import DatadogHeaderProvider

        test_api_key = "test_api_key_12345"
        test_app_key = "test_app_key_12345"
        monkeypatch.setenv("DD_API_KEY", test_api_key)
        monkeypatch.setenv("DD_APP_KEY", test_app_key)
        provider = DatadogHeaderProvider()
        headers = provider.request_headers()
        assert headers == {"DD-API-KEY": test_api_key, "DD-APPLICATION-KEY": test_app_key}
