from contextlib import contextmanager
import json
from unittest import mock

import pytest

from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.ext.test_visibility import _get_default_test_visibility_contrib_config
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityAPIClient
from ddtrace.internal.ci_visibility._api_client import EVPProxyTestVisibilityAPIClient
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.settings._config import Config
from tests.ci_visibility.api_client._util import _AGENTLESS
from tests.ci_visibility.api_client._util import _EVP_PROXY
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _get_mock_connection
from tests.ci_visibility.api_client._util import _get_setting_api_response
from tests.ci_visibility.api_client._util import _get_skippable_api_response
from tests.ci_visibility.api_client._util import _get_tests_api_response
from tests.ci_visibility.test_ci_visibility import _dummy_noop_git_client
from tests.ci_visibility.util import _ci_override_env


@contextmanager
def _patch_env_for_testing():
    """Patches a bunch of things to make the environment more predictable for testing"""
    with _dummy_noop_git_client(), mock.patch(
        "ddtrace.ext.ci._get_runtime_and_os_metadata",
        return_value={
            "os.architecture": "testarch64",
            "os.platform": "Not Actually Linux",
            "os.version": "1.2.3-test",
            "runtime.name": "CPythonTest",
            "runtime.version": "1.2.3",
        },
    ), mock.patch(
        "ddtrace.ext.ci.tags",
        return_value={
            "git.repository_url": "git@github.com:TestDog/dd-test-py.git",
            "git.commit.sha": "mytestcommitsha1234",
            "git.branch": "notmainbranch",
            "git.commit.message": "message",
        },
    ), mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=TestVisibilityAPISettings(),
    ), mock.patch(
        "ddtrace.config._ci_visibility_agentless_enabled", True
    ):
        # Rebuild the config (yes, this is horrible)
        new_ddconfig = Config()
        new_ddconfig._add("test_visibility", _get_default_test_visibility_contrib_config())
        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", new_ddconfig):
            yield


class TestTestVisibilityAPIClient(TestTestVisibilityAPIClientBase):
    requests_mode_parameters = [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS]

    request_mode_settings_parameters = [
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": None,
            "dd_site": None,
            "expected_urls": {
                "setting": "https://api.datadoghq.com/api/v2/libraries/tests/services/setting",
                "skippable": "https://api.datadoghq.com/api/v2/ci/tests/skippable",
                "tests": "https://api.datadoghq.com/api/v2/ci/libraries/tests",
            },
        },
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": None,
            "dd_site": "datad0g.com",
            "expected_urls": {
                "setting": "https://api.datad0g.com/api/v2/libraries/tests/services/setting",
                "skippable": "https://api.datad0g.com/api/v2/ci/tests/skippable",
                "tests": "https://api.datad0g.com/api/v2/ci/libraries/tests",
            },
        },
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": "http://dd",
            "dd_site": None,
            "expected_urls": {
                "setting": "http://dd/api/v2/libraries/tests/services/setting",
                "skippable": "http://dd/api/v2/ci/tests/skippable",
                "tests": "http://dd/api/v2/ci/libraries/tests",
            },
        },
        {
            "mode": _AGENTLESS,
            "api_key": "myfakeapikey",
            "agentless_url": "http://dd",
            "dd_site": "datad0g.com",
            "expected_urls": {
                "setting": "http://dd/api/v2/libraries/tests/services/setting",
                "skippable": "http://dd/api/v2/ci/tests/skippable",
                "tests": "http://dd/api/v2/ci/libraries/tests",
            },
        },
        {
            "mode": _EVP_PROXY,
            "agent_url": "http://myagent:1234",
            "expected_urls": {
                "setting": "http://myagent:1234/evp_proxy/v2/api/v2/libraries/tests/services/setting",
                "skippable": "http://myagent:1234/evp_proxy/v2/api/v2/ci/tests/skippable",
                "tests": "http://myagent:1234/evp_proxy/v2/api/v2/ci/libraries/tests",
            },
        },
    ]

    git_data_parameters = [
        GitData("my_repo_url", "some_branch", "mycommitshaaaaaaalalala", "message"),
        GitData(None, "shalessbranch", None, None),
        GitData("git@gitbob.com:myorg/myrepo.git", "shalessbranch", None, None),
        None,
    ]

    # All requests to setting endpoint are the same within a call of _check_enabled_features()
    expected_items = {
        _AGENTLESS: {
            "endpoints": {
                "setting": "/api/v2/libraries/tests/services/setting",
                "skippable": "/api/v2/ci/tests/skippable",
                "tests": "/api/v2/ci/libraries/tests",
            },
            "headers": {
                "dd-api-key": "myfakeapikey",
                "Content-Type": "application/json",
            },
        },
        _EVP_PROXY: {
            "endpoints": {
                "setting": "/evp_proxy/v2/api/v2/libraries/tests/services/setting",
                "skippable": "/evp_proxy/v2/api/v2/ci/tests/skippable",
                "tests": "/evp_proxy/v2/api/v2/ci/libraries/tests",
            },
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
                requests_mode_settings["expected_urls"]["setting"],
                client_timeout if client_timeout is not None else 12.34,
            )
            mock_connection.request.assert_called_once()
            call_args = mock_connection.request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert call_args[1] == self.expected_items[requests_mode_settings["mode"]]["endpoints"]["setting"]
            assert json.loads(call_args[2]) == self._get_expected_do_request_setting_payload(
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
            assert json.loads(call_args[2]) == self._get_expected_do_request_setting_payload(
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

        mock_connection = _get_mock_connection(_get_skippable_api_response().body)

        with mock.patch(
            "ddtrace.internal.ci_visibility._api_client.get_connection", return_value=mock_connection
        ) as mock_get_connection:
            skippable_items = client.fetch_skippable_items(timeout=request_timeout)
            assert skippable_items == ITRData(correlation_id="1234ideclareacorrelationid")
            mock_get_connection.assert_called_once_with(
                requests_mode_settings["expected_urls"]["skippable"],
                request_timeout if request_timeout is not None else 43.21,
            )
            mock_connection.request.assert_called_once()
            call_args = mock_connection.request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert call_args[1] == self.expected_items[requests_mode_settings["mode"]]["endpoints"]["skippable"]
            assert json.loads(call_args[2]) == self._get_expected_do_request_skippable_payload(
                ITR_SKIPPING_LEVEL.TEST, dd_service="a_test_service", dd_env="a_test_env"
            )
            assert call_args[3] == self.expected_items[requests_mode_settings["mode"]]["headers"]
            mock_connection.close.assert_called_once()

    @pytest.mark.parametrize("client_timeout", [None, 5])
    @pytest.mark.parametrize("request_timeout", [None, 10])
    @pytest.mark.parametrize(
        "requests_mode_settings",
        request_mode_settings_parameters,
    )
    def test_civisibility_api_client_known_tests_do_request(
        self, requests_mode_settings, client_timeout, request_timeout
    ):
        """Tests that the correct payload and headers are sent to the correct API URL for known tests requests"""
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

        mock_connection = _get_mock_connection(_get_tests_api_response().body)

        with mock.patch(
            "ddtrace.internal.ci_visibility._api_client.get_connection", return_value=mock_connection
        ) as mock_get_connection:
            known_tests = client.fetch_known_tests()
            assert known_tests == set()
            mock_get_connection.assert_called_once_with(
                requests_mode_settings["expected_urls"]["tests"],
                client_timeout if client_timeout is not None else 12.34,
            )
            mock_connection.request.assert_called_once()
            call_args = mock_connection.request.call_args_list[0][0]
            assert call_args[0] == "POST"
            assert call_args[1] == self.expected_items[requests_mode_settings["mode"]]["endpoints"]["tests"]
            assert json.loads(call_args[2]) == self._get_expected_do_request_tests_payload(
                dd_service="a_test_service", dd_env="a_test_env"
            )
            assert call_args[3] == self.expected_items[requests_mode_settings["mode"]]["headers"]
            mock_connection.close.assert_called_once()

    @pytest.mark.parametrize(
        "env_vars,expected_config",
        [
            ({}, {}),
            # DD_TRACE_AGENT_URL is ignored in agentless mode
            (
                {"DD_TRACE_AGENT_URL": "http://myagenturl:2468", "DD_SERVICE": "my_test_service1"},
                {"dd_service": "my_test_service1"},
            ),
            (
                {
                    "DD_CIVISIBILITY_AGENTLESS_URL": "https://secureagentless:8080",
                    "DD_SERVICE": "my_test_service2",
                    "DD_ENV": "my_env",
                },
                {"agentless_url": "https://secureagentless:8080", "dd_service": "my_test_service2", "dd_env": "my_env"},
            ),
            ({"DD_ENV": "env_only"}, {"dd_env": "env_only"}),
            ({"DD_SITE": "us5.datad0g.com"}, {"dd_site": "us5.datad0g.com"}),
            (
                {"DD_TAGS": "test.configuration.disk:slow,test.configuration.memory:low"},
                {"custom_configurations": {"disk": "slow", "memory": "low"}},
            ),
        ],
    )
    @pytest.mark.parametrize("itr_skipping_level", [ITR_SKIPPING_LEVEL.TEST, ITR_SKIPPING_LEVEL.SUITE])
    def test_civisibility_api_client_agentless_env_config_success(self, env_vars, expected_config, itr_skipping_level):
        """Tests that the agentless API client is configured correctly based on environment

        Whether the client behaves properly based on these configuration items (eg: proper use of base url, etc.) is
        tested in other methods.
        """
        # NOTE: we copy the fixtures so that we don't mutate the originals
        _env_vars = env_vars.copy()
        _expected_config = expected_config.copy()

        _env_vars.update({"DD_CIVISIBILITY_AGENTLESS_ENABLED": "true", "DD_API_KEY": "api_key_for_testing"})
        if itr_skipping_level == ITR_SKIPPING_LEVEL.SUITE:
            _env_vars["_DD_CIVISIBILITY_ITR_SUITE_MODE"] = "true"
        configurations = {
            "os.architecture": "testarch64",
            "os.platform": "Not Actually Linux",
            "os.version": "1.2.3-test",
            "runtime.name": "CPythonTest",
            "runtime.version": "1.2.3",
        }
        if "custom_configurations" in _expected_config:
            configurations["custom"] = _expected_config.pop("custom_configurations")
        if "dd_service" not in _expected_config:
            _expected_config["dd_service"] = "dd-test-py"
        if "dd_env" not in _expected_config:
            _expected_config["dd_env"] = "none"

        git_data = GitData("git@github.com:TestDog/dd-test-py.git", "notmainbranch", "mytestcommitsha1234", "message")
        with _ci_override_env(_env_vars, full_clear=True), _patch_env_for_testing():
            try:
                expected_client = AgentlessTestVisibilityAPIClient(
                    itr_skipping_level=itr_skipping_level,
                    configurations=configurations,
                    git_data=git_data,
                    api_key="api_key_for_testing",
                    **_expected_config,
                )
                CIVisibility.enable()
                assert CIVisibility.enabled is True
                assert CIVisibility._instance is not None
                assert CIVisibility._instance._api_client is not None
                assert CIVisibility._instance._api_client.__dict__ == expected_client.__dict__
            finally:
                CIVisibility.disable()

    @pytest.mark.parametrize(
        "env_vars,expected_config",
        [
            # Default env should result in default config with EVP client
            ({}, {}),
            # DD_API_KEY should be ignored if not agentless
            ({"DD_API_KEY": "api_key_for_testing"}, {}),
            (
                {"DD_TAGS": "test.configuration.disk:slow,test.configuration.memory:low", "DD_SERVICE": "not_ddtestpy"},
                {
                    "custom_configurations": {"disk": "slow", "memory": "low"},
                    "dd_service": "not_ddtestpy",
                },
            ),
        ],
    )
    @pytest.mark.parametrize("itr_skipping_level", [ITR_SKIPPING_LEVEL.TEST, ITR_SKIPPING_LEVEL.SUITE])
    def test_civisibility_api_client_evp_proxy_config_success(self, env_vars, expected_config, itr_skipping_level):
        """Tests that the EVP Proxy API client is configured correctly based on environment

        Whether the client behaves properly based on these configuration items (eg: proper use of base url, etc.) is
        tested in other methods.
        """
        # NOTE: we copy the fixtures so that we don't mutate the originals
        _env_vars = env_vars.copy()
        _expected_config = expected_config.copy()

        if itr_skipping_level == ITR_SKIPPING_LEVEL.SUITE:
            _env_vars["_DD_CIVISIBILITY_ITR_SUITE_MODE"] = "true"
        configurations = {
            "os.architecture": "testarch64",
            "os.platform": "Not Actually Linux",
            "os.version": "1.2.3-test",
            "runtime.name": "CPythonTest",
            "runtime.version": "1.2.3",
        }
        if "custom_configurations" in _expected_config:
            configurations["custom"] = _expected_config.pop("custom_configurations")
        if "dd_service" not in _expected_config:
            _expected_config["dd_service"] = "dd-test-py"

        git_data = GitData("git@github.com:TestDog/dd-test-py.git", "notmainbranch", "mytestcommitsha1234", "message")
        with _ci_override_env(_env_vars, full_clear=True), _patch_env_for_testing(), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_base_url",
            return_value=EVP_PROXY_AGENT_BASE_PATH,
        ), mock.patch(
            "ddtrace.settings._agent.config.trace_agent_url", return_value="http://shouldntbeused:6218"
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddtrace.tracer._span_aggregator.writer.intake_url",
            "http://patchedagenturl:6218",
        ):
            try:
                expected_client = EVPProxyTestVisibilityAPIClient(
                    itr_skipping_level=itr_skipping_level,
                    configurations=configurations,
                    git_data=git_data,
                    agent_url="http://patchedagenturl:6218",
                    dd_env="none",
                    **_expected_config,
                )
                CIVisibility.enable()
                assert CIVisibility.enabled is True
                assert CIVisibility._instance is not None
                assert CIVisibility._instance._api_client is not None

                assert CIVisibility._instance._api_client.__dict__ == expected_client.__dict__
            finally:
                CIVisibility.disable()

    def test_civisibility_api_client_evp_respects_agent_default_config(self):
        """Tests that, if no DD_ENV is provided in EVP mode, the agent's default env is used"""
        agent_info_response = json.loads(
            """
            {
              "version": "7.49.1",
              "git_commit": "1790cab",
              "endpoints": [
                "/v0.3/traces",
                "/v0.3/services",
                "/v0.4/traces",
                "/v0.4/services",
                "/v0.5/traces",
                "/v0.7/traces",
                "/profiling/v1/input",
                "/telemetry/proxy/",
                "/v0.6/stats",
                "/v0.1/pipeline_stats",
                "/evp_proxy/v1/",
                "/evp_proxy/v2/",
                "/evp_proxy/v3/",
                "/debugger/v1/input",
                "/debugger/v1/diagnostics",
                "/symdb/v1/input",
                "/dogstatsd/v1/proxy",
                "/dogstatsd/v2/proxy",
                "/v0.7/config",
                "/config/set"
              ],
              "client_drop_p0s": true,
              "span_meta_structs": true,
              "long_running_spans": true,
              "config": {
                "default_env": "not_the_default_default_env",
                "target_tps": 10,
                "max_eps": 200,
                "receiver_port": 8126,
                "receiver_socket": "",
                "connection_limit": 0,
                "receiver_timeout": 0,
                "max_request_bytes": 26214400,
                "statsd_port": 8125,
                "max_memory": 0,
                "max_cpu": 0,
                "analyzed_spans_by_service": {},
                "obfuscation": {
                  "elastic_search": true,
                  "mongo": true,
                  "sql_exec_plan": false,
                  "sql_exec_plan_normalize": false,
                  "http": {
                    "remove_query_string": false,
                    "remove_path_digits": false
                  },
                  "remove_stack_traces": false,
                  "redis": {
                    "Enabled": true,
                    "RemoveAllArgs": false
                  },
                  "memcached": {
                    "Enabled": true,
                    "KeepCommand": false
                  }
                }
              }
            }
            """
        )

        configurations = {
            "os.architecture": "testarch64",
            "os.platform": "Not Actually Linux",
            "os.version": "1.2.3-test",
            "runtime.name": "CPythonTest",
            "runtime.version": "1.2.3",
        }

        git_data = GitData("git@github.com:TestDog/dd-test-py.git", "notmainbranch", "mytestcommitsha1234", "message")
        with _ci_override_env(full_clear=True), _patch_env_for_testing(), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._agent_evp_proxy_base_url",
            return_value=EVP_PROXY_AGENT_BASE_PATH,
        ), mock.patch("ddtrace.internal.agent.info", return_value=agent_info_response), mock.patch(
            "ddtrace.settings._agent.config.trace_agent_url",
            new_callable=mock.PropertyMock,
            return_value="http://shouldntbeused:6218",
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddtrace.tracer._span_aggregator.writer.intake_url",
            "http://patchedagenturl:6218",
        ):
            try:
                expected_client = EVPProxyTestVisibilityAPIClient(
                    itr_skipping_level=ITR_SKIPPING_LEVEL.TEST,
                    configurations=configurations,
                    git_data=git_data,
                    agent_url="http://patchedagenturl:6218",
                    dd_env="not_the_default_default_env",
                    dd_service="dd-test-py",
                )
                CIVisibility.enable()
                assert CIVisibility.enabled is True
                assert CIVisibility._instance is not None
                assert CIVisibility._instance._api_client is not None

                assert CIVisibility._instance._api_client.__dict__ == expected_client.__dict__
            finally:
                CIVisibility.disable()
