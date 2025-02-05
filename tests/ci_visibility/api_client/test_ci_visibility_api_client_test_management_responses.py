from http.client import RemoteDisconnected
import json
import socket
import textwrap
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import TestProperties
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _make_fqdn_internal_test_id


class TestTestVisibilityAPIClientTestManagementResponses(TestTestVisibilityAPIClientBase):
    """Tests that Test Management tests responses from the API client are parsed properly"""

    def test_api_client_test_management_tests_parsed(self):
        response_dict = {
            "data": {
                "id": "J0ucvcSApX8",
                "type": "ci_app_libraries_tests",
                "attributes": {
                    "modules": {
                        "module1": {
                            "suites": {
                                "suite1.py": {
                                    "tests": {
                                        "test1": {"properties": {"quarantined": True}},
                                        "test2": {"properties": {"quarantined": False}},
                                        "test3": {"properties": {}},
                                        "test4": {},
                                    }
                                },
                                "suite2.py": {
                                    "tests": {
                                        "test1": {"properties": {"quarantined": False, "disabled": True}},
                                        "test5": {"properties": {"quarantined": True}},
                                    }
                                },
                            }
                        },
                        "module2": {
                            "suites": {
                                "suite1.py": {
                                    "tests": {
                                        "test1": {"properties": {"quarantined": False}},
                                        "test2": {"properties": {"disabled": False}},
                                    }
                                }
                            }
                        },
                    }
                },
            }
        }
        mock_response = Response(200, json.dumps(response_dict))

        expected_tests = {
            _make_fqdn_internal_test_id("module1", "suite1.py", "test1"): TestProperties(
                quarantined=True, disabled=False
            ),
            _make_fqdn_internal_test_id("module1", "suite1.py", "test2"): TestProperties(
                quarantined=False, disabled=False
            ),
            _make_fqdn_internal_test_id("module1", "suite1.py", "test3"): TestProperties(
                quarantined=False, disabled=False
            ),
            _make_fqdn_internal_test_id("module1", "suite1.py", "test4"): TestProperties(
                quarantined=False, disabled=False
            ),
            _make_fqdn_internal_test_id("module1", "suite2.py", "test1"): TestProperties(
                quarantined=False, disabled=True
            ),
            _make_fqdn_internal_test_id("module1", "suite2.py", "test5"): TestProperties(
                quarantined=True, disabled=False
            ),
            _make_fqdn_internal_test_id("module2", "suite1.py", "test1"): TestProperties(
                quarantined=False, disabled=False
            ),
            _make_fqdn_internal_test_id("module2", "suite1.py", "test2"): TestProperties(
                quarantined=False, disabled=False
            ),
        }

        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=mock_response):
            assert client.fetch_test_management_tests() == expected_tests

    @pytest.mark.parametrize(
        "do_request_side_effect",
        [
            TimeoutError,
            socket.timeout,
            RemoteDisconnected,
            Response(403),
            Response(500),
            Response(200, "this is not json"),
            Response(200, '{"valid_json": "invalid_structure"}'),
            Response(200, '{"errors": "there was an error"}'),
            Response(
                200,
                textwrap.dedent(
                    """
                {
                    "data": {
                    "id": "J0ucvcSApX8",
                    "type": "ci_app_libraries_tests",
                    "attributes": {
                        "potatoes_but_not_tests": {}
                        }
                    }
                }
            """
                ),
            ),
        ],
    )
    def test_api_client_test_management_tests_errors(self, do_request_side_effect):
        """Tests that the client correctly handles errors in the Test Management test API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", side_effect=[do_request_side_effect]):
            settings = client.fetch_test_management_tests()
            assert settings is None
