from http.client import RemoteDisconnected
import json
import socket
from unittest import mock

import pytest

from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.api_client._util import TestTestVisibilityAPIClientBase
from tests.ci_visibility.api_client._util import _make_fqdn_suite_ids
from tests.ci_visibility.api_client._util import _make_fqdn_test_ids


class TestTestVisibilityAPIClientSkippableResponses(TestTestVisibilityAPIClientBase):
    """Tests that skippable responses from the API client are parsed properly"""

    @pytest.mark.parametrize(
        "skippable_response,expected_skippable_items",
        [
            (Response(200, json.dumps({"data": [], "meta": {}})), ITRData()),
            (
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(correlation_id="1234ideclareacorrelationid"),
            ),
        ],
    )
    def test_civisibility_api_client_skippable_parsed_correlation_id(
        self, skippable_response, expected_skippable_items
    ):
        """Tests that the client reports errors correctly based on the API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=skippable_response):
            actual_skippable_items = client.fetch_skippable_items()
            assert actual_skippable_items == expected_skippable_items

    @pytest.mark.parametrize(
        "skippable_response,expected_skippable_items",
        [
            (Response(200, json.dumps({"data": [], "meta": {}})), ITRData()),
            (  # Simple case with two tests in the same bundle
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "12345",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "my_test_1",
                                    },
                                },
                                {
                                    "id": "54321",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"debug": "False"}, "metadata": {}}',
                                    },
                                },
                            ],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(
                    correlation_id="1234ideclareacorrelationid",
                    skippable_items=_make_fqdn_test_ids(
                        [
                            ("my_test_bundle", "my_test_suite_1.py", "my_test_1"),
                            (
                                "my_test_bundle",
                                "my_test_suite_1.py",
                                "MyClass1::my_test_2",
                                '{"arguments": {"debug": "False"}, "metadata": {}}',
                            ),
                        ]
                    ),
                ),
            ),
            (  # Case with items to ignore and more bundles
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "12345",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_1"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "my_test_1",
                                    },
                                },
                                {
                                    "id": "54321",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_1"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"debug": "False"}, "metadata": {}}',
                                    },
                                },
                                {
                                    "id": "23456",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                                    },
                                },
                                {  # Unparsable item should not show up (name is missing)
                                    "id": "54321",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                        "parameters": '{"arguments": {"debug": "False"}, "metadata": {}}',
                                    },
                                },
                                {  # Is properly formatted, but of type suite, should not show up
                                    "id": "23456",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                                    },
                                },
                                {  # Is a duplicate, should not show up twice
                                    "id": "23456",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                                    },
                                },
                            ],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(
                    correlation_id="1234ideclareacorrelationid",
                    skippable_items=_make_fqdn_test_ids(
                        [
                            ("my_test_bundle_1", "my_test_suite_1.py", "my_test_1"),
                            (
                                "my_test_bundle_1",
                                "my_test_suite_1.py",
                                "MyClass1::my_test_2",
                                '{"arguments": {"debug": "False"}, "metadata": {}}',
                            ),
                            (
                                "my_test_bundle_2",
                                "my_test_suite_1.py",
                                "MyClass1::my_test_2",
                                '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                            ),
                        ]
                    ),
                ),
            ),
        ],
    )
    def test_civisibility_api_client_skippable_parsed_tests_with_parameters(
        self, skippable_response, expected_skippable_items
    ):
        """Tests that the client reports errors correctly based on the API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=skippable_response):
            actual_skippable_items = client.fetch_skippable_items()
            assert actual_skippable_items == expected_skippable_items

    @pytest.mark.parametrize(
        "skippable_response,expected_skippable_items",
        [
            (Response(200, json.dumps({"data": [], "meta": {}})), ITRData()),
            (  # Simple case with two tests in the same bundle
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "12345",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "my_test_1",
                                    },
                                },
                                {
                                    "id": "54321",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"debug": "False"}, "metadata": {}}',
                                    },
                                },
                            ],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(
                    correlation_id="1234ideclareacorrelationid",
                    skippable_items=_make_fqdn_test_ids(
                        [
                            ("my_test_bundle", "my_test_suite_1.py", "my_test_1"),
                            (
                                "my_test_bundle",
                                "my_test_suite_1.py",
                                "MyClass1::my_test_2",
                            ),
                        ]
                    ),
                ),
            ),
            (  # Case with items to ignore and more bundles
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "12345",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_1"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "my_test_1",
                                    },
                                },
                                {
                                    "id": "54321",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_1"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"debug": "False"}, "metadata": {}}',
                                    },
                                },
                                {
                                    "id": "23456",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                                    },
                                },
                                {  # Unparsable item should not show up (name is missing)
                                    "id": "54321",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                        "parameters": '{"arguments": {"debug": "False"}, "metadata": {}}',
                                    },
                                },
                                {  # Is properly formatted, but of type suite, should not show up
                                    "id": "23456",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                                    },
                                },
                                {  # Is a duplicate, should not show up twice
                                    "id": "23456",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                        "name": "MyClass1::my_test_2",
                                        "parameters": '{"arguments": {"should_pass": "True"}, "metadata": {}}',
                                    },
                                },
                            ],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(
                    correlation_id="1234ideclareacorrelationid",
                    skippable_items=_make_fqdn_test_ids(
                        [
                            ("my_test_bundle_1", "my_test_suite_1.py", "my_test_1"),
                            (
                                "my_test_bundle_1",
                                "my_test_suite_1.py",
                                "MyClass1::my_test_2",
                            ),
                            (
                                "my_test_bundle_2",
                                "my_test_suite_1.py",
                                "MyClass1::my_test_2",
                            ),
                        ]
                    ),
                ),
            ),
        ],
    )
    def test_civisibility_api_client_skippable_parsed_tests_without_parameters(
        self, skippable_response, expected_skippable_items
    ):
        """Tests that the client reports errors correctly based on the API response"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=skippable_response):
            actual_skippable_items = client.fetch_skippable_items(ignore_test_parameters=True)
            assert actual_skippable_items == expected_skippable_items

    @pytest.mark.parametrize(
        "skippable_response,expected_skippable_items",
        [
            (Response(200, json.dumps({"data": [], "meta": {}})), ITRData()),
            (
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "12345",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_1.py",
                                    },
                                },
                                {
                                    "id": "54321",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle"},
                                        "suite": "my_test_suite_2.py",
                                    },
                                },
                            ],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(
                    correlation_id="1234ideclareacorrelationid",
                    skippable_items=_make_fqdn_suite_ids(
                        [
                            ("my_test_bundle", "my_test_suite_1.py"),
                            ("my_test_bundle", "my_test_suite_2.py"),
                        ]
                    ),
                ),
            ),
            (
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [
                                {
                                    "id": "12345",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_1"},
                                        "suite": "my_test_suite_1.py",
                                    },
                                },
                                {
                                    "id": "54321",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_1"},
                                        "suite": "my_test_suite_2.py",
                                    },
                                },
                                {
                                    "id": "23456",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_1.py",
                                    },
                                },
                                {
                                    "id": "65432",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_2.py",
                                    },
                                },
                                {  # This is a duplicate and should not result in a duplicate item
                                    "id": "34567",
                                    "type": "suite",
                                    "attributes": {
                                        "configurations": {"test.bundle": "my_test_bundle_2"},
                                        "suite": "my_test_suite_2.py",
                                    },
                                },
                                {  # This is unparsable and should not show up as an item
                                    "id": "75463",
                                    "type": "suite",
                                },
                                {  # This is a test and should not show up as an item
                                    "id": "54678",
                                    "type": "test",
                                    "attributes": {
                                        "configurations": {"test.bundle": "test_bundle_should_not_appear"},
                                        "suite": "test_suite_should_not_appear.py",
                                        "parameters": '{"parameters": "should not matter"',
                                        "name": "name should not matter",
                                    },
                                },
                            ],
                            "meta": {
                                "correlation_id": "1234ideclareacorrelationid",
                            },
                        }
                    ),
                ),
                ITRData(
                    correlation_id="1234ideclareacorrelationid",
                    skippable_items=_make_fqdn_suite_ids(
                        [
                            ("my_test_bundle_1", "my_test_suite_1.py"),
                            ("my_test_bundle_1", "my_test_suite_2.py"),
                            ("my_test_bundle_2", "my_test_suite_1.py"),
                            ("my_test_bundle_2", "my_test_suite_2.py"),
                        ]
                    ),
                ),
            ),
        ],
    )
    def test_civisibility_api_client_skippable_parsed_suites(self, skippable_response, expected_skippable_items):
        """Tests that the client reports errors correctly based on the API response"""
        client = self._get_test_client(itr_skipping_level=ITR_SKIPPING_LEVEL.SUITE)
        with mock.patch.object(client, "_do_request", return_value=skippable_response):
            actual_skippable_items = client.fetch_skippable_items()
            assert actual_skippable_items == expected_skippable_items

    @pytest.mark.parametrize(
        "skippable_response,expected_skippable_items",
        [
            (Response(200, json.dumps({"data": [], "meta": {}})), ITRData()),
            (
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [],
                            "meta": {
                                "coverage": {
                                    "file_in_root.py": "RERERAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                    "file/not_in_root/one_to_99.py": "f///////////////8AAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                    "tests/empty_coverage.py": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                    "tests/empty_init_like.py": "gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                    "tests/lots_of_lines.py": "RtXGZUxWdMTlTkZc1GVMx1bEbVxmVMVnTE5U5GXNRlTMdWREwQIE",
                                },
                                "correlation_id": "why_not_test_this_while_were_here",
                            },
                        }
                    ),
                ),
                ITRData(
                    covered_files={
                        "file_in_root.py": CoverageLines.from_list([1, 5, 9, 13, 17, 21, 25, 29]),
                        "file/not_in_root/one_to_99.py": CoverageLines.from_list(list(range(1, 100))),
                        "tests/empty_coverage.py": CoverageLines.from_list([]),
                        "tests/empty_init_like.py": CoverageLines.from_list([0]),
                        "tests/lots_of_lines.py": CoverageLines.from_list(
                            list(range(1, 292, 4)) + list(range(1, 275, 5)) + list(range(1, 311, 7))
                        ),
                    },
                    correlation_id="why_not_test_this_while_were_here",
                ),
            ),
            (
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [],
                            "meta": {
                                "coverage": {
                                    "path_matters_not": "because this is not b64",
                                    "file/not_in_root/one_to_99.py": "f///////////////8AAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                },
                            },
                        }
                    ),
                ),
                ITRData(covered_files={"file/not_in_root/one_to_99.py": CoverageLines.from_list(list(range(1, 100)))}),
            ),
            (
                Response(
                    200,
                    json.dumps(
                        {
                            "data": [],
                            "meta": {
                                "coverage": {
                                    "path_matters_not": "because this is not b64",
                                },
                                "correlation_id": "why_not_test_this_while_were_here",
                            },
                        }
                    ),
                ),
                ITRData(correlation_id="why_not_test_this_while_were_here", covered_files={}),
            ),
        ],
    )
    def test_civisibility_api_client_skippable_parsed_covered_files(self, skippable_response, expected_skippable_items):
        """Tests that the client correctly parses covered files, and does not raise exceptions when it cannot"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", return_value=skippable_response):
            actual_skippable_items = client.fetch_skippable_items()
            assert actual_skippable_items == expected_skippable_items

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
        ],
    )
    def test_civisibility_api_client_skippable_errors(self, do_request_side_effect):
        """Tests that the client reports errors correctly gives a None item without crashing"""
        client = self._get_test_client()
        with mock.patch.object(client, "_do_request", side_effect=[do_request_side_effect]):
            skippable_items = client.fetch_skippable_items()
            assert skippable_items is None
