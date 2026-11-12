"""NOTE: this lives in its own file simply because some of the test variables are unwieldy"""

from http.client import RemoteDisconnected
import socket
import textwrap
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility.telemetry import early_flake_detection
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _get_tests_api_response
from tests.ci_visibility.api_client._util import _make_fqdn_test_ids


class TestTestVisibilityAPIClientKnownTestResponses(TestTestVisibilityAPIClientBase):
    """Tests that known tests responses from the API client are parsed properly"""

    @pytest.mark.parametrize(
        "known_test_response,expected_tests",
        [
            # Defaults
            (_get_tests_api_response({}), set()),
            # Single item
            (
                _get_tests_api_response({"module1": {"suite1.py": ["test1"]}}),
                set(_make_fqdn_test_ids([("module1", "suite1.py", "test1")])),
            ),
            # Multiple known items
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
    def test_civisibility_api_client_known_tests_parsed(self, known_test_response, expected_tests):
        """Tests that the client correctly returns known tests from API response"""
        client = self._get_test_client()
        with (
            mock.patch.object(client, "_do_request", return_value=known_test_response),
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_tests_count"
            ) as mock_count,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_pages_fetched"
            ) as mock_pages,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_fetch_ms"
            ) as mock_fetch_ms,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_request_ms"
            ) as mock_request_ms,
        ):
            assert client.fetch_known_tests(read_from_cache=False) == expected_tests
            # Verify pagination metrics were recorded
            mock_count.assert_called_once_with(len(expected_tests))
            mock_pages.assert_called_once_with(1)
            mock_fetch_ms.assert_called_once()
            mock_request_ms.assert_called_once()

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
        """Tests that the client correctly handles errors in the known test API response"""
        client = self._get_test_client()
        with (
            mock.patch.object(client, "_do_request", side_effect=[do_request_side_effect] * 5),
            mock.patch("ddtrace.internal.utils.retry.sleep"),
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_tests_count"
            ) as mock_count,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_pages_fetched"
            ) as mock_pages,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_fetch_ms"
            ) as mock_fetch_ms,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_request_ms"
            ) as mock_request_ms,
        ):
            settings = client.fetch_known_tests(read_from_cache=False)
            assert settings is None
            # No pagination metrics should be emitted on failure
            mock_count.assert_not_called()
            mock_pages.assert_not_called()
            mock_fetch_ms.assert_not_called()
            mock_request_ms.assert_not_called()

    def test_civisibility_api_client_known_tests_paginated(self):
        client = self._get_test_client()
        response_page_1 = _get_tests_api_response(
            {"module1": {"suite1.py": ["test1"]}},
            page_info={"cursor": "page-2", "size": 1, "has_next": True},
        )
        response_page_2 = _get_tests_api_response(
            {"module1": {"suite1.py": ["test2"]}},
            page_info={"cursor": None, "size": 1, "has_next": False},
        )

        with (
            mock.patch.object(client, "_do_request", side_effect=[response_page_1, response_page_2]),
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_tests_count"
            ) as mock_count,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_pages_fetched"
            ) as mock_pages,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_fetch_ms"
            ) as mock_fetch_ms,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_request_ms"
            ) as mock_request_ms,
        ):
            expected_tests = set(
                _make_fqdn_test_ids([("module1", "suite1.py", "test1"), ("module1", "suite1.py", "test2")])
            )
            assert client.fetch_known_tests(read_from_cache=False) == expected_tests
            # Verify 2 pages were fetched and timing metrics were recorded
            mock_count.assert_called_once_with(2)
            mock_pages.assert_called_once_with(2)
            mock_fetch_ms.assert_called_once()
            mock_request_ms.assert_called_once()
            # Total fetch time should be >= sum of request times
            total_fetch_ms = mock_fetch_ms.call_args[0][0]
            total_request_ms = mock_request_ms.call_args[0][0]
            assert total_fetch_ms >= total_request_ms

    @pytest.mark.parametrize("fail_second_page", [False, True])
    def test_pagination_metrics_measure_page_calls_and_discard_partial_fetches(self, fail_second_page):
        client = self._get_test_client()
        first_page = {
            "data": {
                "attributes": {"tests": {"mod": {"suite": ["test"]}}, "page_info": {"has_next": True, "cursor": "next"}}
            }
        }
        second_page = {"data": {"attributes": {"tests": {"mod": {"suite": ["test"]}}}}}
        watches = [mock.Mock(elapsed=mock.Mock(return_value=seconds)) for seconds in [10.0, 1.25, 2.5]]
        with (
            mock.patch.object(
                client,
                "_do_request_with_telemetry",
                side_effect=[first_page, TimeoutError() if fail_second_page else second_page],
            ) as request,
            mock.patch("ddtrace.internal.ci_visibility._api_client.StopWatch", side_effect=watches),
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_pages_fetched"
            ) as pages,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_fetch_ms"
            ) as fetch_ms,
            mock.patch(
                "ddtrace.internal.ci_visibility._api_client.record_early_flake_detection_total_request_ms"
            ) as request_ms,
        ):
            result = client.fetch_known_tests(read_from_cache=False)

        assert request.call_count == 2
        if fail_second_page:
            assert result is None
            pages.assert_not_called()
            fetch_ms.assert_not_called()
            request_ms.assert_not_called()
        else:
            assert result == set(_make_fqdn_test_ids([("mod", "suite", "test")]))
            pages.assert_called_once_with(2)
            fetch_ms.assert_called_once_with(10000.0)
            request_ms.assert_called_once_with(3750.0)

    @pytest.mark.parametrize(
        "method,metric,value",
        [
            ("record_early_flake_detection_pages_fetched", "known_tests.pages_fetched", 2),
            ("record_early_flake_detection_total_fetch_ms", "known_tests.total_fetch_ms", 10000.0),
            ("record_early_flake_detection_total_request_ms", "known_tests.total_request_ms", 3750.0),
        ],
    )
    def test_pagination_metric_names(self, method, metric, value):
        with mock.patch.object(early_flake_detection, "telemetry_writer") as writer:
            getattr(early_flake_detection, method)(value)

        writer.add_distribution_metric.assert_called_once_with(TELEMETRY_NAMESPACE.CIVISIBILITY, metric, value)
