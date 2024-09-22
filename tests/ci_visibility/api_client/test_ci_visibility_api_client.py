import json
import re
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.git_data import GitData
from tests.ci_visibility.api_client._util import _AGENTLESS
from tests.ci_visibility.api_client._util import _EVP_PROXY
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _get_mock_connection
from tests.ci_visibility.api_client._util import _get_setting_api_response
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import _get_default_civisibility_ddconfig


class TestTestVisibilityAPIClient(TestTestVisibilityAPIClientBase):
    requests_mode_parameters = [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS]

    request_mode_settings_parameters = [
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": None,
            "dd_site": None,
            "expected_url": "https://api.datadoghq.com/api/v2/libraries/tests/services/setting",
        },
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": None,
            "dd_site": "datad0g.com",
            "expected_url": "https://api.datad0g.com/api/v2/libraries/tests/services/setting",
        },
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": "http://dd",
            "dd_site": None,
            "expected_url": "http://dd/api/v2/libraries/tests/services/setting",
        },
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": "http://dd",
            "dd_site": "datad0g.com",
            "expected_url": "http://dd/api/v2/libraries/tests/services/setting",
        },
        {
            "mode": _EVP_PROXY,
            "agent_url": "http://myagent:1234",
            "expected_url": "http://myagent:1234/evp_proxy/v2/api/v2/libraries/tests/services/setting",
        },
    ]

    git_data_parameters = [
        GitData("my_repo_url", "some_branch", "mycommitshaaaaaaalalala"),
        GitData(None, "shalessbranch", None),
        GitData("git@gitbob.com:myorg/myrepo.git", "shalessbranch", None),
        None,
    ]

    # All requests to setting endpoint are the same within a call of _check_enabled_features()
    expected_do_request_method = "POST"
    expected_do_request_urls = {
        REQUESTS_MODE.AGENTLESS_EVENTS: re.compile(
            r"^https://api\.datad0g\.com/api/v2/libraries/tests/services/setting$"
        ),
        REQUESTS_MODE.EVP_PROXY_EVENTS: re.compile(
            r"^http://notahost:1234/evp_proxy/v2/api/v2/libraries/tests/services/setting$"
        ),
    }
    expected_items = {
        _AGENTLESS: {
            "endpoint": "/api/v2/libraries/tests/services/setting",
            "headers": {
                "dd-api-key": "myfakeapikey",
                "Content-Type": "application/json",
            },
        },
        _EVP_PROXY: {
            "endpoint": "/evp_proxy/v2/api/v2/libraries/tests/services/setting",
            "headers": {
                "X-Datadog-EVP-Subdomain": "api",
                "Content-Type": "application/json",
            },
        },
    }

    @pytest.mark.parametrize("client_timeout", [None, 5])
    @pytest.mark.parametrize(
        "requests_mode_settings",
        request_mode_settings_parameters,
    )
    def test_civisibility_api_client_settings_do_request_connection(self, client_timeout, requests_mode_settings):
        """Tests that the correct payload and headers are sent to the correct API URL for settings requests"""

        client = self._get_test_client(
            requests_mode=requests_mode_settings["mode"],
            api_key=requests_mode_settings.get("api_key"),
            dd_site=requests_mode_settings.get("dd_site"),
            agentless_url=requests_mode_settings.get("agentless_url"),
            agent_url=requests_mode_settings.get("agent_url"),
            dd_service="a_test_service",
            dd_env="a_test_env",
            client_timeout=client_timeout,
        )

        mock_connection = _get_mock_connection(_get_setting_api_response().body)

        with mock.patch(
            "ddtrace.internal.ci_visibility._api_client.get_connection", return_value=mock_connection
        ) as mock_get_connection:
            settings = client.fetch_settings()
            assert settings == TestVisibilityAPISettings()
            mock_get_connection.assert_called_once_with(
                requests_mode_settings["expected_url"], client_timeout if client_timeout is not None else 12.34
            )
            mock_connection.request.assert_called_once()
            call_args = mock_connection.request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert call_args[1] == self.expected_items[requests_mode_settings["mode"]]["endpoint"]
            assert json.loads(call_args[2]) == self._get_expected_do_request_payload(
                ITR_SKIPPING_LEVEL.TEST, dd_service="a_test_service", dd_env="a_test_env"
            )
            assert call_args[3] == self.expected_items[requests_mode_settings["mode"]]["headers"]
            mock_connection.close.assert_called_once()

    @pytest.mark.parametrize("itr_skipping_level", [ITR_SKIPPING_LEVEL.TEST, ITR_SKIPPING_LEVEL.SUITE])
    @pytest.mark.parametrize("dd_service", [None, "My.Test_service"])
    @pytest.mark.parametrize("dd_env", [None, "My.Test_env"])
    @pytest.mark.parametrize("git_data", git_data_parameters)
    def test_civisibility_api_client_settings_do_request_call_optionals(
        self, itr_skipping_level, git_data, dd_service, dd_env
    ):
        """Tests that the correct payload is passed to _do_request when optional parameters are set

        NOTE: this does not re-test URL/header/etc differences between agentless and EVP proxy as that is already tested
        by test_civisibility_api_client_settings_do_request_connection
        """
        client = self._get_test_client(
            itr_skipping_level=itr_skipping_level,
            api_key="my_api_key",
            dd_service=dd_service,
            dd_env=dd_env,
            git_data=git_data,
        )
        with mock.patch.object(client, "_do_request", return_value=_get_setting_api_response()) as mock_do_request:
            settings = client.fetch_settings()
            assert settings == TestVisibilityAPISettings()

            assert mock_do_request.call_count == 1
            call_args = mock_do_request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert json.loads(call_args[2]) == self._get_expected_do_request_payload(
                itr_skipping_level, git_data=git_data, dd_service=dd_service, dd_env=dd_env
            )

    @pytest.mark.parametrize("client_timeout", [None, 5])
    @pytest.mark.parametrize("request_timeout", [None, 10])
    @pytest.mark.parametrize(
        "requests_mode_settings",
        request_mode_settings_parameters,
    )
    def test_civisibility_api_client_skippable_do_request(
        self, requests_mode_settings, client_timeout, request_timeout
    ):
        """Tests that the correct payload and headers are sent to the correct API URL for skippable requests"""
        pass

        client = self._get_test_client(
            requests_mode=requests_mode_settings["mode"],
            api_key=requests_mode_settings.get("api_key"),
            dd_site=requests_mode_settings.get("dd_site"),
            agentless_url=requests_mode_settings.get("agentless_url"),
            agent_url=requests_mode_settings.get("agent_url"),
            dd_service="a_test_service",
            dd_env="a_test_env",
            client_timeout=client_timeout,
        )

        mock_connection = _get_mock_connection(_get_setting_api_response().body)

        with mock.patch(
            "ddtrace.internal.ci_visibility._api_client.get_connection", return_value=mock_connection
        ) as mock_get_connection:
            settings = client.fetch_settings()
            assert settings == TestVisibilityAPISettings()
            mock_get_connection.assert_called_once_with(
                requests_mode_settings["expected_url"], client_timeout if client_timeout is not None else 12.34
            )
            mock_connection.request.assert_called_once()
            call_args = mock_connection.request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert call_args[1] == self.expected_items[requests_mode_settings["mode"]]["endpoint"]
            assert json.loads(call_args[2]) == self._get_expected_do_request_payload(
                ITR_SKIPPING_LEVEL.TEST, dd_service="a_test_service", dd_env="a_test_env"
            )
            assert call_args[3] == self.expected_items[requests_mode_settings["mode"]]["headers"]
            mock_connection.close.assert_called_once()

    def test_civisibility_api_client_agentless_config(self):
        """Tests that the agentless API client is configured correctly"""
        assert False

    def test_civisibility_api_client_evpproxy_config(self):
        """Tests that the EVP Proxy API client is configured correctly"""
        assert False

    @pytest.mark.parametrize(
        "dd_civisibility_agentless_url, expected_url",
        [
            ("", "https://api.datad0g.com/api/v2/libraries/tests/services/setting"),
            ("https://bar.foo:1234", "https://bar.foo:1234/api/v2/libraries/tests/services/setting"),
        ],
    )
    def test_civisibility_check_enabled_feature_respects_civisibility_agentless_url(
        self, dd_civisibility_agentless_url, expected_url
    ):
        """Tests that DD_CIVISIBILITY_AGENTLESS_URL is respected when set"""
        with _ci_override_env(
            dict(
                DD_API_KEY="foobar.baz",
                DD_CIVISIBILITY_AGENTLESS_URL=dd_civisibility_agentless_url,
                DD_CIVISIBILITY_AGENTLESS_ENABLED="1",
            )
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            side_effect=[_get_setting_api_response(200, False, False, False, False)],
        ) as mock_do_request:
            mock_civisibility = self._get_mock_civisibility(REQUESTS_MODE.AGENTLESS_EVENTS, False)
            _ = mock_civisibility._check_enabled_features()

            assert mock_do_request.call_args_list[0][0][1] == expected_url
