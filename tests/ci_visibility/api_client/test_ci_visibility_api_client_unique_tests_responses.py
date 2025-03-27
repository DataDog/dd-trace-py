"""NOTE: this lives in its own file simply because some of the test variables are unwieldy"""
from http.client import RemoteDisconnected
import socket
import textwrap
from unittest import mock

import pytest

from ddtrace.internal.utils.http import Response
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _get_tests_api_response
from tests.ci_visibility.api_client._util import _make_fqdn_test_ids


class TestTestVisibilityAPIClientUniqueTestResponses(TestTestVisibilityAPIClientBase):
    """Tests that unique tests responses from the API client are parsed properly"""

    @pytest.mark.parametrize(
        "unique_test_response,expected_tests",
        [
            # Defaults
            (_get_tests_api_response({}), set()),
            # Single item
            (
                _get_tests_api_response({"module1": {"suite1.py": ["test1"]}}),
                set(_make_fqdn_test_ids([("module1", "suite1.py", "test1")])),
            ),
            # Multiple unique items
            (
                _get_tests_api_response({"module1": {"suite1.py": ["test1"]}}),
                set(_make_fqdn_test_ids([("module1", "suite1.py", "test1")])),
            ),
            (
                _get_tests_api_response({"module1": {"suite1.py": ["test1"]}, "module2": {"suite2.py": ["test2"]}}),
                set(_make_fqdn_test_ids([("module1", "suite1.py", "test1"), ("module2", "suite2.py", "test2")])),
            ),
            # Multiple items with same name
            (
                _get_tests_api_response(
                    {
                        "module1": {
                            "suite1.py": ["test1", "test2"],
                            "suite2.py": ["test1"],
                        },
                        "module2": {
                            "suite1.py": ["test1"],
                            "suite2.py": ["test1", "test2"],
                        },
                    }
                ),
                set(
                    _make_fqdn_test_ids(
                        [
                            ("module1", "suite1.py", "test1"),
                            ("module1", "suite1.py", "test2"),
                            ("module1", "suite2.py", "test1"),
                            ("module2", "suite1.py", "test1"),
                            ("module2", "suite2.py", "test1"),
                            ("module2", "suite2.py", "test2"),
                        ]
                    )
                ),
            ),
        ],
    )
    def test_civisibility_api_client_known_tests_parsed(self, unique_test_response, expected_tests):
        """Tests that the client correctly returns unique tests from API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=unique_test_response):
            assert client.fetch_known_tests() == expected_tests

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
    def test_civisibility_api_client_known_tests_errors(self, do_request_side_effect):
        """Tests that the client correctly handles errors in the unique test API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", side_effect=[do_request_side_effect]):
            settings = client.fetch_known_tests()
            assert settings is None
