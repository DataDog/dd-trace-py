from json import JSONDecodeError
from unittest import mock

import pytest

import ddtrace
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility._api_client import _TestVisibilityAPIClientBase
from ddtrace.internal.ci_visibility.git_client import METADATA_UPLOAD_STATUS
from ddtrace.internal.ci_visibility.git_client import CIVisibilityGitClient


class TestCheckEnabledFeatures:
    """Test whether CIVisibility._check_enabled_features properly waits for git metadata upload as necessary

    Across a "matrix" of:
    - whether the settings API returns {... "require_git": true ...}
    - call failures
    """

    @staticmethod
    def _get_mock_civisibility():
        with mock.patch.object(CIVisibility, "__init__", return_value=None):
            mock_civisibility = CIVisibility()

            # User-configurable values
            mock_civisibility._api_client = mock.Mock(spec=_TestVisibilityAPIClientBase)
            mock_civisibility._api_client.fetch_settings.side_effect = []

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
            mock_civisibility.tracer = mock.Mock(spec=ddtrace.trace.Tracer)
            mock_civisibility.tracer._agent_url = "http://notahost:1234"

        return mock_civisibility

    @pytest.mark.parametrize(
        "setting_response, expected_result",
        [
            ((True, True, False, True), (True, True, False, True)),
            ((True, False, False, True), (True, False, False, True)),
            ((False, True, False, True), (False, True, False, True)),
            ((False, False, False, False), (False, False, False, False)),
        ],
    )
    @pytest.mark.parametrize("suite_skipping_mode", [True, False])
    def test_civisibility_check_enabled_features_require_git_false(
        self, setting_response, expected_result, suite_skipping_mode
    ):
        """This essentially tests the passthrough nature of _check_enabled_features when require_git is False"""
        mock_civisibility = self._get_mock_civisibility()
        mock_civisibility._api_client.fetch_settings.side_effect = [TestVisibilityAPISettings(*setting_response)]

        enabled_features = mock_civisibility._check_enabled_features()

        assert enabled_features == TestVisibilityAPISettings(
            expected_result[0],
            expected_result[1],
            False,
            expected_result[3],
        )

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
        wait_for_upload_side_effect,
        setting_response,
        expected_result,
        second_setting_require_git,
        suite_skipping_mode,
    ):
        """Simulates the scenario where we would run coverage because we don't have git metadata, but after the upload
        finishes, we see we don't need to run coverage.

        The test matrix covers the possible cases where:
        - coverage starts off true, then gets disabled after metadata upload (in which case skipping cannot be enabled)
        - coverage starts off true, then stays true
        - coverage starts off false, and stays false

        Finally, require_git on the second attempt is tested both as True and False, but the response should not affect
        the final settings

        Note: the None values in the setting_response parameters is because the git_require parameter is set explicitly
        in the test body
        """

        mock_civisibility = self._get_mock_civisibility()
        mock_civisibility._git_client.wait_for_metadata_upload_status.side_effect = wait_for_upload_side_effect
        mock_civisibility._api_client.fetch_settings.side_effect = [
            TestVisibilityAPISettings(setting_response[0][0], setting_response[0][1], True, setting_response[0][3]),
            TestVisibilityAPISettings(
                setting_response[1][0], setting_response[1][1], second_setting_require_git, setting_response[1][3]
            ),
        ]
        enabled_features = mock_civisibility._check_enabled_features()

        mock_civisibility._git_client.wait_for_metadata_upload_status.assert_called_once()

        assert enabled_features == TestVisibilityAPISettings(
            expected_result[0], expected_result[1], second_setting_require_git, expected_result[3]
        )

    @pytest.mark.parametrize(
        "first_fetch_settings_side_effect",
        [
            TimeoutError,
            JSONDecodeError("Expecting value", "line 1", 1),
            KeyError,
            ValueError("API response status code: 600"),
            ValueError("Settings response contained an error, disabling Intelligent Test Runner"),
            TestVisibilityAPISettings(True, True, True, True),
            TestVisibilityAPISettings(True, False, True, True),
            TestVisibilityAPISettings(False, False, True, True),
        ],
    )
    @pytest.mark.parametrize(
        "second_fetch_settings_side_effect",
        [
            TimeoutError,
            JSONDecodeError("Expecting value", "line 1", 1),
            KeyError,
            ValueError("API response status code: 600"),
        ],
    )
    def test_civisibility_check_enabled_features_any_api_error_disables_itr(
        self, first_fetch_settings_side_effect, second_fetch_settings_side_effect
    ):
        """Tests that any error encountered while querying the setting endpoint results in ITR
        being disabled, whether on the first or second request
        """
        mock_civisibility = self._get_mock_civisibility()
        mock_civisibility._git_client.wait_for_metadata_upload_status.side_effect = [METADATA_UPLOAD_STATUS.SUCCESS]

        mock_civisibility._api_client.fetch_settings.side_effect = [
            first_fetch_settings_side_effect,
            second_fetch_settings_side_effect,
        ]

        enabled_features = mock_civisibility._check_enabled_features()

        assert enabled_features == TestVisibilityAPISettings(False, False, False, False)
