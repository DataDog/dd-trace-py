import json
import re
from unittest import mock

import pytest

import ddtrace
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityClient
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import REQUESTS_MODE
from ddtrace.internal.ci_visibility.git_client import METADATA_UPLOAD_STATUS
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import _get_default_civisibility_ddconfig


class EvpProxyTestVisibilityClient:
    pass


class TestCheckEnabledFeatures:
    """Test whether CIVisibility._check_enabled_features properly
    - properly calls _do_request (eg: payloads are correct)
    - waits for git metadata upload as necessary

    Across a "matrix" of:
    - whether the settings API returns {... "require_git": true ...}
    - call failures
    """

    requests_mode_parameters = [REQUESTS_MODE.AGENTLESS_EVENTS, REQUESTS_MODE.EVP_PROXY_EVENTS]

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
    expected_do_request_headers = {
        REQUESTS_MODE.AGENTLESS_EVENTS: {
            "dd-api-key": "myfakeapikey",
            "Content-Type": "application/json",
        },
        REQUESTS_MODE.EVP_PROXY_EVENTS: {
            "X-Datadog-EVP-Subdomain": "api",
            "Content-Type": "application/json",
        },
    }

    @staticmethod
    def _get_expected_do_request_payload(suite_skipping_mode=False):
        return {
            "data": {
                "id": "checkoutmyuuid4",
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "test_level": "suite" if suite_skipping_mode else "test",
                    "service": "service",
                    "env": None,
                    "repository_url": "my_repo_url",
                    "sha": "mycommitshaaaaaaalalala",
                    "branch": "notmain",
                    "configurations": {
                        "os.architecture": "arm64",
                        "os.platform": "PlatForm",
                        "os.version": "9.8.a.b",
                        "runtime.name": "RPython",
                        "runtime.version": "11.5.2",
                    },
                },
            }
        }

    @staticmethod
    def _get_mock_civisibility_client(self, agentless=False, do_requests_mocks=None):
        mock_class = AgentlessTestVisibilityClient if agentless else EvpProxyTestVisibilityClient
        mock_api_client = mock.Mock(spec=mock_class)
        if agentless:
            mock_class
            mock_api_client._do_request = mock.Mock()
        else:
            mock_api_client._do_request = mock.Mock()

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
                mock_civisibility._api_client = mock.Mock(spec=EvpProxyTestVisibilityClient)

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
        status_code,
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

    def _check_mock_do_request_calls(self, mock_do_request, count, requests_mode, suite_skipping_mode):
        assert mock_do_request.call_count == count

        for c in range(count):
            call_args = mock_do_request.call_args_list[c][0]
            assert call_args[0] == self.expected_do_request_method
            assert self.expected_do_request_urls[requests_mode].match(call_args[1])
            assert call_args[3] == self.expected_do_request_headers[requests_mode]

            payload = json.loads(call_args[2])
            assert payload == self._get_expected_do_request_payload(suite_skipping_mode)

    @pytest.fixture(scope="function", autouse=True)
    def _test_context_manager(self):
        with mock.patch("ddtrace.internal.ci_visibility._api_client.uuid4", return_value="checkoutmyuuid4"):
            yield

    @pytest.mark.parametrize("requests_mode", requests_mode_parameters)
    @pytest.mark.parametrize(
        "setting_response, expected_result",
        [
            ((True, True, None, True), (True, True, None, True)),
            ((True, False, None, True), (True, False, None, True)),
            ((False, True, None, True), (False, True, None, True)),
            ((False, False, None, False), (False, False, None, False)),
        ],
    )
    @pytest.mark.parametrize("suite_skipping_mode", [True, False])
    def test_civisibility_check_enabled_features_require_git_false(
        self, requests_mode, setting_response, expected_result, suite_skipping_mode
    ):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            side_effect=[
                self._get_settings_api_response(
                    200, setting_response[0], setting_response[1], False, setting_response[3]
                )
            ],
        ) as mock_do_request:
            mock_civisibility = self._get_mock_civisibility(requests_mode, suite_skipping_mode)
            enabled_features = mock_civisibility._check_enabled_features()

            self._check_mock_do_request_calls(mock_do_request, 1, requests_mode, suite_skipping_mode)

            assert enabled_features == TestVisibilityAPISettings(
                expected_result[0], expected_result[1], False, expected_result[3]
            )

    @pytest.mark.parametrize("requests_mode", requests_mode_parameters)
    @pytest.mark.parametrize(
        "wait_for_upload_side_effect",
        [[METADATA_UPLOAD_STATUS.SUCCESS], [METADATA_UPLOAD_STATUS.FAILED], ValueError, TimeoutError],
    )
    @pytest.mark.parametrize(
        "setting_response, expected_result",
        [
            ([(True, True, None, True), (True, True, None, True)], (True, True, None, True)),
            ([(True, False, None, True), (True, False, None, True)], (True, False, None, True)),
            ([(True, False, None, True), (False, False, None, True)], (False, False, None, True)),
            ([(False, False, None, False), (False, False, None, False)], (False, False, None, False)),
        ],
    )
    @pytest.mark.parametrize("second_setting_require_git", [False, True])
    @pytest.mark.parametrize("suite_skipping_mode", [True, False])
    def test_civisibility_check_enabled_features_require_git_true(
        self,
        requests_mode,
        wait_for_upload_side_effect,
        setting_response,
        expected_result,
        second_setting_require_git,
        suite_skipping_mode,
    ):
        """Simulates the scenario where we would run coverage because we don't have git metadata, but after the upload
        finishes, we see we don't need to run coverage.

        Along with the requests mode dimension, the test matrix covers the possible cases where:
        - coverage starts off true, then gets disabled after metadata upload (in which case skipping cannot be enabled)
        - coverage starts off true, then stays true
        - coverage starts off false, and stays false

        Finally, require_git on the second attempt is tested both as True and False, but the response should not affect
        the final settings

        Note: the None values in the setting_response parameters is because the git_require parameter is set explicitly
        in the test body
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
            side_effect=[
                self._get_settings_api_response(
                    200, setting_response[0][0], setting_response[0][1], True, setting_response[0][3]
                ),
                self._get_settings_api_response(
                    200,
                    setting_response[1][0],
                    setting_response[1][1],
                    second_setting_require_git,
                    setting_response[1][3],
                ),
            ],
        ) as mock_do_request:
            mock_civisibility = self._get_mock_civisibility(requests_mode, suite_skipping_mode)
            mock_civisibility._git_client.wait_for_metadata_upload_status.side_effect = wait_for_upload_side_effect
            enabled_features = mock_civisibility._check_enabled_features()

            mock_civisibility._git_client.wait_for_metadata_upload_status.assert_called_once()

            self._check_mock_do_request_calls(mock_do_request, 2, requests_mode, suite_skipping_mode)

            assert enabled_features == TestVisibilityAPISettings(
                expected_result[0], expected_result[1], second_setting_require_git, expected_result[3]
            )

    @pytest.mark.parametrize("requests_mode", requests_mode_parameters)
    @pytest.mark.parametrize(
        "first_do_request_side_effect",
        [
            TimeoutError,
            Response(status=200, body="} this is bad JSON"),
            Response(status=200, body='{"not correct key": "not correct value"}'),
            Response(status=600, body="Only status code matters here"),
            Response(
                status=200,
                body='{"errors":["Not found"]}',
            ),
            (200, True, True, True, True),
            (200, True, False, True, True),
            (200, False, False, True, True),
        ],
    )
    @pytest.mark.parametrize(
        "second_do_request_side_effect",
        [
            TimeoutError,
            Response(status=200, body="} this is bad JSON"),
            Response(status=200, body='{"not correct key": "not correct value"}'),
            Response(status=600, body="Only status code matters here"),
        ],
    )
    def test_civisibility_check_enabled_features_any_api_error_disables_itr(
        self, requests_mode, first_do_request_side_effect, second_do_request_side_effect
    ):
        """Tests that any error encountered while querying the setting endpoint results in ITR
        being disabled, whether on the first or second request
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder._do_request",
        ) as mock_do_request:
            if isinstance(first_do_request_side_effect, tuple):
                expected_call_count = 2
                mock_do_request.side_effect = [
                    self._get_settings_api_response(*first_do_request_side_effect),
                    second_do_request_side_effect,
                ]
            else:
                expected_call_count = 1
                mock_do_request.side_effect = [first_do_request_side_effect]

            mock_civisibility = self._get_mock_civisibility(requests_mode, False)
            mock_civisibility._git_client.wait_for_metadata_upload_status.side_effect = [METADATA_UPLOAD_STATUS.SUCCESS]

            enabled_features = mock_civisibility._check_enabled_features()

            assert mock_do_request.call_count == expected_call_count
            assert enabled_features == TestVisibilityAPISettings(False, False, False, False)

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
