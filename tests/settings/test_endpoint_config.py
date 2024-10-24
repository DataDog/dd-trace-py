#!/usr/bin/env python3

import mock
from tests.utils import override_env
from ddtrace.settings.endpoint_config import fetch_config_from_endpoint


def test_unset_config_endpoint():
    with override_env({}):
        assert fetch_config_from_endpoint() == {}


def test_set_config_endpoint():
    # Mock http.request response
    with override_env({"DD_CONFIG_ENDPOINT": "http://localhost:8000"}), mock.patch(
        "urllib3.PoolManager.request"
    ) as mock_request:
        mock_request.return_value.data = b'{"dd_iast_enabled": true}'

        assert fetch_config_from_endpoint() == {"dd_iast_enabled": True}
