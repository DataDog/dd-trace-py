import json
import re
import typing as t
from unittest import mock

import pytest

import ddtrace
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityClient
from ddtrace.internal.ci_visibility._api_client import EVPProxyTestVisibilityClient
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import _get_default_civisibility_ddconfig


_AGENTLESS = REQUESTS_MODE.AGENTLESS_EVENTS
_EVP_PROXY = REQUESTS_MODE.EVP_PROXY_EVENTS


def _get_mock_connection(body):
    class _mock_http_response:
        status = 200

        def read(self):
            return body

    mock_connection = mock.Mock()
    mock_connection.getresponse.return_value = _mock_http_response()
    mock_connection.request = mock.Mock()
    mock_connection.close = mock.Mock()

    return mock_connection


class TestTestVisibilityAPIClient:
    """Tests that the TestVisibility API clients
    - make calls to the correct backend URL
    - send the correct payload
    - handle API responses correctly

    Across a "matrix" of:
    - requests mode (agentless, EVP proxy)
    - overrides (custom agent URL, custom agentless URL)
    - good/bad/incorrect API responses
    """

    default_configurations = {
        "os.architecture": "arm64",
        "os.platform": "PlatForm",
        "os.version": "9.8.a.b",
        "runtime.name": "RPython",
        "runtime.version": "11.5.2",
    }

    requests_mode_parameters = [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS]

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

    def _get_test_client(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL = ITR_SKIPPING_LEVEL.TEST,
        requests_mode: REQUESTS_MODE = _AGENTLESS,
        git_data: GitData = None,
        api_key: t.Optional[str] = "my_api_key",
        dd_site: t.Optional[str] = None,
        agentless_url: t.Optional[str] = None,
        agent_url: t.Optional[str] = "http://agenturl:1234",
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
        client_timeout: t.Optional[float] = None,
    ):
        git_data = git_data if git_data is not None else self.git_data_parameters[0]

        if requests_mode == _AGENTLESS:
            return AgentlessTestVisibilityClient(
                itr_skipping_level,
                git_data,
                self.default_configurations,
                api_key,
                dd_site,
                agentless_url,
                dd_service,
                dd_env,
                client_timeout,
            )
        else:
            return EVPProxyTestVisibilityClient(
                itr_skipping_level,
                git_data,
                self.default_configurations,
                agent_url,
                dd_service,
                dd_env,
                client_timeout,
            )

    def _get_expected_do_request_payload(
        self,
        itr_skipping_level: ITR_SKIPPING_LEVEL = ITR_SKIPPING_LEVEL.TEST,
        git_data: GitData = None,
        dd_service: t.Optional[str] = None,
        dd_env: t.Optional[str] = None,
    ):
        git_data = self.git_data_parameters[0] if git_data is None else git_data

        return {
            "data": {
                "id": "checkoutmyuuid4",
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "test_level": "test" if itr_skipping_level == ITR_SKIPPING_LEVEL.TEST else "suite",
                    "service": dd_service,
                    "env": dd_env,
                    "repository_url": git_data.repository_url,
                    "sha": git_data.commit_sha,
                    "branch": git_data.branch,
                    "configurations": {
                        "os.architecture": "arm64",
                        "os.platform": "PlatForm",
                        "os.version": "9.8.a.b",
                        "runtime.name": "RPython",
                        "runtime.version": "11.5.2",
                    },
                },
            },
        }

    @staticmethod
    def _get_mock_civisibility(requests_mode, suite_skipping_mode):
        with mock.patch.object(CIVisibility, "__init__", return_value=None):
            mock_civisibility = CIVisibility()

            # User-configurable values
            mock_civisibility._requests_mode = requests_mode
            mock_civisibility._suite_skipping_mode = suite_skipping_mode
            if requests_mode == REQUESTS_MODE.AGENTLESS_EVENTS:
                mock_civisibility._api_client = mock.Mock(spec=AgentlessTestVisibilityClient)
            else:
                mock_civisibility._api_client = mock.Mock(spec=EVPProxyTestVisibilityClient)

            # Defaults
            mock_civisibility._service = "service"
            mock_civisibility._api_key = "myfakeapikey"
            mock_civisibility._dd_site = "datad0g.com"
            mock_civisibility._tags = {
                ci.git.REPOSITORY_URL: "my_repo_url",
                ci.git.COMMIT_SHA: "mycommitshaaaaaaalalala",
                ci.git.BRANCH: "notmain",
            }
            mock_civisibility._configurations = {
                "os.architecture": "arm64",
                "os.platform": "PlatForm",
                "os.version": "9.8.a.b",
                "runtime.name": "RPython",
                "runtime.version": "11.5.2",
            }
            mock_civisibility._git_client = mock.Mock(spec=CIVisibilityGitClient)
            mock_civisibility.tracer = mock.Mock(spec=ddtrace.Tracer)
            mock_civisibility.tracer._agent_url = "http://notahost:1234"

        return mock_civisibility

    @staticmethod
    def _get_settings_api_response(
        status_code=200,
        code_coverage=False,
        tests_skipping=False,
        require_git=False,
        itr_enabled=False,
        efd_detection_enabled=False,
        efd_5s=10,
        efd_10s=5,
        efd_30s=3,
        efd_5m=2,
        efd_session_threshold=30.0,
        flaky_test_retries_enabled=False,
    ):
        return Response(
            status=status_code,
            body=json.dumps(
                {
                    "data": {
                        "id": "1234",
                        "type": "ci_app_tracers_test_service_settings",
                        "attributes": {
                            "code_coverage": code_coverage,
                            "early_flake_detection": {
                                "enabled": efd_detection_enabled,
                                "slow_test_retries": {"10s": efd_10s, "30s": efd_30s, "5m": efd_5m, "5s": efd_5s},
                                "faulty_session_threshold": efd_session_threshold,
                            },
                            "flaky_test_retries_enabled": flaky_test_retries_enabled,
                            "itr_enabled": itr_enabled,
                            "require_git": require_git,
                            "tests_skipping": tests_skipping,
                        },
                    }
                }
            ),
        )

    @pytest.fixture(scope="function", autouse=True)
    def _test_context_manager(self):
        with mock.patch("ddtrace.internal.ci_visibility._api_client.uuid4", return_value="checkoutmyuuid4"), mock.patch(
            "ddtrace.internal.ci_visibility._api_client.DEFAULT_TIMEOUT", 12.34
        ):
            yield

    @pytest.mark.parametrize("client_timeout", [None, 5])
    @pytest.mark.parametrize(
        "requests_mode_settings",
        [
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
        ],
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

        mock_connection = _get_mock_connection(self._get_settings_api_response().body)

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
        with mock.patch.object(
            client, "_do_request", return_value=self._get_settings_api_response()
        ) as mock_do_request:
            settings = client.fetch_settings()
            assert settings == TestVisibilityAPISettings()

            assert mock_do_request.call_count == 1
            call_args = mock_do_request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert json.loads(call_args[2]) == self._get_expected_do_request_payload(
                itr_skipping_level, git_data=git_data, dd_service=dd_service, dd_env=dd_env
            )

    def test_civisibility_api_client_skippable_do_request(self):
        """Tests that the correct payload and headers are sent to the correct API URL for skippable requests"""
        pass

    def test_civisibility_api_client_agentless_bad_api_key(self):
        """Tests that bad API key setups either cause expected errors"""
        pass

    def test_civisibility_api_client_agentless_config(self):
        """Tests that the agentless API client is configured correctly"""
        pass

    def test_civisibility_api_client_evpproxy_config(self):
        """Tests that the EVP Proxy API client is configured correctly"""
        pass

    def test_civisibility_api_client_settings_errors(self):
        """Tests that the client reports errors correctly based on the API response"""
        pass

    def test_civisibility_api_client_skippable_errors(self):
        """Tests that the client reports errors correctly based on the API response"""
        pass

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
            side_effect=[self._get_settings_api_response(200, False, False, False, False)],
        ) as mock_do_request:
            mock_civisibility = self._get_mock_civisibility(REQUESTS_MODE.AGENTLESS_EVENTS, False)
            _ = mock_civisibility._check_enabled_features()

            assert mock_do_request.call_args_list[0][0][1] == expected_url
