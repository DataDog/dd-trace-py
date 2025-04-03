"""NOTE: this lives in its own file simply because some of the test variables are unwieldy"""
from http.client import RemoteDisconnected
from json import JSONDecodeError
import socket
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.errors import CIVisibilityAuthenticationException
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _get_setting_api_response


class TestTestVisibilityAPIClientSettingResponses(TestTestVisibilityAPIClientBase):
    """Tests that setting responses from the API client are parsed properly"""

    @pytest.mark.parametrize(
        "setting_response, expected_settings",
        [
            # Defaults
            [_get_setting_api_response(), TestVisibilityAPISettings()],
            # "Basics" all enabled
            [
                _get_setting_api_response(code_coverage=True, tests_skipping=True, require_git=True, itr_enabled=True),
                TestVisibilityAPISettings(
                    coverage_enabled=True, skipping_enabled=True, require_git=True, itr_enabled=True
                ),
            ],
            # Matrix-y-testing
            [
                _get_setting_api_response(code_coverage=True, tests_skipping=True),
                TestVisibilityAPISettings(coverage_enabled=True, skipping_enabled=True),
            ],
            [
                _get_setting_api_response(tests_skipping=True, itr_enabled=True),
                TestVisibilityAPISettings(skipping_enabled=True, itr_enabled=True),
            ],
            [
                _get_setting_api_response(code_coverage=True, itr_enabled=True),
                TestVisibilityAPISettings(coverage_enabled=True, itr_enabled=True),
            ],
            # Only flaky test retries
            [
                _get_setting_api_response(flaky_test_retries_enabled=True),
                TestVisibilityAPISettings(flaky_test_retries_enabled=True),
            ],
            # EFD defaults
            [
                _get_setting_api_response(
                    tests_skipping=True, itr_enabled=True, efd_detection_enabled=True, known_tests_enabled=True
                ),
                TestVisibilityAPISettings(
                    skipping_enabled=True,
                    itr_enabled=True,
                    early_flake_detection=EarlyFlakeDetectionSettings(enabled=True),
                    known_tests_enabled=True,
                ),
            ],
            # EFD matrix-y-testing
            # EFD should have default values regardless of whether it's present in the response
            [
                _get_setting_api_response(tests_skipping=True, itr_enabled=True, efd_present=False),
                TestVisibilityAPISettings(skipping_enabled=True, itr_enabled=True),
            ],
            [
                _get_setting_api_response(tests_skipping=True, itr_enabled=True, efd_present=True),
                TestVisibilityAPISettings(skipping_enabled=True, itr_enabled=True),
            ],
            [
                _get_setting_api_response(
                    code_coverage=True,
                    flaky_test_retries_enabled=True,
                    efd_detection_enabled=True,
                    known_tests_enabled=True,
                    efd_5s=10,
                    efd_10s=25,
                    efd_30s=15,
                    efd_5m=10,
                    faulty_session_threshold=45,
                ),
                TestVisibilityAPISettings(
                    coverage_enabled=True,
                    flaky_test_retries_enabled=True,
                    early_flake_detection=EarlyFlakeDetectionSettings(
                        enabled=True,
                        slow_test_retries_5s=10,
                        slow_test_retries_10s=25,
                        slow_test_retries_30s=15,
                        slow_test_retries_5m=10,
                        faulty_session_threshold=45,
                    ),
                    known_tests_enabled=True,
                ),
            ],
            # If EFD is not enabled, the defaults should be returned even if the response has values (because we
            # ignore whatever other values are in the response on the assumption they will not be used)
            [
                _get_setting_api_response(
                    efd_detection_enabled=False,
                    efd_5s=1,
                    efd_10s=2,
                    efd_30s=3,
                    efd_5m=4,
                    faulty_session_threshold=5,
                ),
                TestVisibilityAPISettings(
                    early_flake_detection=EarlyFlakeDetectionSettings(
                        enabled=False,
                    ),
                    known_tests_enabled=False,
                ),
            ],
        ],
    )
    def test_civisibility_api_client_setting_parsed(self, setting_response, expected_settings):
        """Tests that the client reports errors correctly based on the API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=setting_response):
            assert client.fetch_settings() == expected_settings

    @pytest.mark.parametrize(
        "do_request_side_effect,expected_exception",
        [
            [TimeoutError, TimeoutError],
            [socket.timeout, socket.timeout],
            [RemoteDisconnected, RemoteDisconnected],
            [Response(403), CIVisibilityAuthenticationException],
            [Response(500), ValueError],
            [Response(200, "this is not json"), JSONDecodeError],
            [Response(200, '{"valid_json": "invalid_structure"}'), KeyError],
            [Response(200, '{"errors": "there was an error"}'), ValueError],
        ],
    )
    def test_civisibility_api_client_setting_errors(self, do_request_side_effect, expected_exception):
        """Tests that the client reports errors correctly based on the API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", side_effect=[do_request_side_effect]), pytest.raises(
            expected_exception
        ):
            settings = client.fetch_settings()
            assert settings is None
