import json
import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility._api_client import _CONFIGURATIONS_TYPE
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityAPIClient
from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.ci_visibility.telemetry.api_request import APIRequestMetricNames
from ddtrace.internal.utils.http import Response
from tests.ci_visibility.api_client._util import _get_setting_api_response


class TestAPIClientCaching:
    """Tests for the caching functionality in _TestVisibilityAPIClientBase"""

    @pytest.fixture(autouse=True)
    def setup_test_env(self):
        """Setup test environment with temporary directories"""
        # Create a temporary directory to serve as our test working directory
        self.test_cwd = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_cwd)

        # Default test data
        self.git_data = GitData("my_repo_url", "some_branch", "mycommitsha", "some message")
        self.configurations: _CONFIGURATIONS_TYPE = {
            "os.architecture": "arm64",
            "os.platform": "PlatForm",
            "os.version": "9.8.a.b",
            "runtime.name": "RPython",
            "runtime.version": "11.5.2",
        }

        yield

        # Cleanup
        os.chdir(self.original_cwd)
        shutil.rmtree(self.test_cwd, ignore_errors=True)

    def _get_test_client(self):
        """Helper to create a test API client"""
        return AgentlessTestVisibilityAPIClient(
            itr_skipping_level=ITR_SKIPPING_LEVEL.TEST,
            git_data=self.git_data,
            configurations=self.configurations,
            api_key="test_api_key",
            dd_service="test_service",
            dd_env="test_env",
            timeout=15.0,
        )

    def test_cache_key_generation(self):
        """Test that cache keys are generated correctly and consistently"""
        client = self._get_test_client()

        method = "POST"
        endpoint = "/api/v2/libraries/tests/services/setting"
        payload = '{"data": {"test": "value"}}'

        # Generate cache key
        cache_key1 = client._get_cache_key(method, endpoint, payload)
        cache_key2 = client._get_cache_key(method, endpoint, payload)

        # Same parameters should generate same cache key
        assert cache_key1 == cache_key2
        assert len(cache_key1) == 64  # SHA256 hex digest length

        # Different payload should generate different cache key
        different_payload = '{"data": {"test": "different_value"}}'
        cache_key3 = client._get_cache_key(method, endpoint, different_payload)
        assert cache_key1 != cache_key3

    def test_cache_key_includes_all_parameters(self):
        """Test that cache key changes when any parameter changes"""
        client = self._get_test_client()

        base_method = "POST"
        base_endpoint = "/api/v2/libraries/tests/services/setting"
        base_payload = '{"data": {"test": "value"}}'

        base_key = client._get_cache_key(base_method, base_endpoint, base_payload)

        # Different method
        method_key = client._get_cache_key("GET", base_endpoint, base_payload)
        assert base_key != method_key

        # Different endpoint
        endpoint_key = client._get_cache_key(base_method, "/different/endpoint", base_payload)
        assert base_key != endpoint_key

        # Different payload
        payload_key = client._get_cache_key(base_method, base_endpoint, '{"different": "payload"}')
        assert base_key != payload_key

    def test_cache_file_path_generation(self):
        """Test that cache file paths are generated correctly"""
        client = self._get_test_client()

        cache_key = "abc123def456"
        cache_path = client._get_cache_file_path(cache_key)

        expected_dir = os.path.join(self.test_cwd, ".ddtrace_api_cache")
        expected_path = os.path.join(expected_dir, f"{cache_key}.json")

        assert cache_path == expected_path
        # Directory should be created
        assert os.path.exists(expected_dir)

    def test_cache_write_and_read(self):
        """Test that data can be written to and read from cache"""
        client = self._get_test_client()

        cache_key = "test_cache_key"
        test_data = {"result": "cached_response", "status": "success"}

        # Write to cache
        client._write_to_cache(cache_key, test_data)

        # Read from cache
        cached_data = client._read_from_cache(cache_key)

        assert cached_data == test_data

    def test_cache_miss_returns_none(self):
        """Test that cache miss returns None"""
        client = self._get_test_client()

        non_existent_key = "this_key_does_not_exist"
        cached_data = client._read_from_cache(non_existent_key)

        assert cached_data is None

    def test_first_request_makes_http_call_and_caches_response(self):
        """Test that the first request makes an HTTP call and caches the response"""
        client = self._get_test_client()

        # Mock API response
        api_response: Response = _get_setting_api_response()
        assert isinstance(api_response.body, str)
        expected_parsed_response = json.loads(api_response.body)

        # Mock _do_request to return the response without making actual HTTP call
        with patch.object(client, "_do_request", return_value=api_response) as mock_do_request:
            metric_names = APIRequestMetricNames(
                count="test.count", duration="test.duration", response_bytes=None, error="test.error"
            )

            method = "POST"
            endpoint = "/api/v2/libraries/tests/services/setting"
            payload = '{"data": {"test": "payload"}}'

            # First request should make HTTP call
            result = client._do_request_with_telemetry(method, endpoint, payload, metric_names)

            # Verify HTTP call was made
            mock_do_request.assert_called_once_with(method, endpoint, payload, timeout=None)
            assert result == expected_parsed_response

            # Verify response was cached
            cache_key = client._get_cache_key(method, endpoint, payload)
            cached_data = client._read_from_cache(cache_key)
            assert cached_data == expected_parsed_response

    def test_second_request_uses_cached_response(self):
        """Test that the second identical request uses cached response without HTTP call"""
        client = self._get_test_client()

        # Mock API response for first call
        api_response = _get_setting_api_response()
        assert isinstance(api_response.body, str)
        expected_parsed_response = json.loads(api_response.body)

        # Mock _do_request to return the response without making actual HTTP call
        with patch.object(client, "_do_request", return_value=api_response) as mock_do_request:
            metric_names = APIRequestMetricNames(
                count="test.count", duration="test.duration", response_bytes=None, error="test.error"
            )

            method = "POST"
            endpoint = "/api/v2/libraries/tests/services/setting"
            payload = '{"data": {"test": "payload"}}'

            # First request
            result1 = client._do_request_with_telemetry(method, endpoint, payload, metric_names)
            assert result1 == expected_parsed_response

            # Reset mock to track second call
            mock_do_request.reset_mock()

            # Second identical request
            result2 = client._do_request_with_telemetry(method, endpoint, payload, metric_names)

            # Verify no HTTP call was made for second request
            mock_do_request.assert_not_called()

            # Verify same result
            assert result2 == expected_parsed_response
            assert result1 == result2

    def test_different_requests_not_cached_together(self):
        """Test that different requests don't interfere with each other's cache"""
        client = self._get_test_client()

        # Mock responses for different requests
        response1 = _get_setting_api_response(code_coverage=True)
        response2 = _get_setting_api_response(code_coverage=False)
        assert isinstance(response1.body, str)
        assert isinstance(response2.body, str)
        expected_parsed_response1 = json.loads(response1.body)
        expected_parsed_response2 = json.loads(response2.body)

        # Mock _do_request to return different responses based on payload
        with patch.object(client, "_do_request", side_effect=[response1, response2]) as mock_do_request:
            metric_names = APIRequestMetricNames(
                count="test.count", duration="test.duration", response_bytes=None, error="test.error"
            )

            method = "POST"
            endpoint = "/api/v2/libraries/tests/services/setting"
            payload1 = '{"data": {"test": "payload1"}}'
            payload2 = '{"data": {"test": "payload2"}}'

            # First request
            result1 = client._do_request_with_telemetry(method, endpoint, payload1, metric_names)
            assert result1 == expected_parsed_response1

            # Second request with different payload should make HTTP call
            result2 = client._do_request_with_telemetry(method, endpoint, payload2, metric_names)
            assert result2 == expected_parsed_response2

            # Verify both HTTP calls were made (different payloads)
            assert mock_do_request.call_count == 2

            # Verify both are cached separately
            cache_key1 = client._get_cache_key(method, endpoint, payload1)
            cache_key2 = client._get_cache_key(method, endpoint, payload2)

            assert client._read_from_cache(cache_key1) == expected_parsed_response1
            assert client._read_from_cache(cache_key2) == expected_parsed_response2

    def test_cache_handles_invalid_json_gracefully(self):
        """Test that cache operations handle invalid JSON gracefully"""
        client = self._get_test_client()

        cache_key = "test_invalid_json"
        cache_file = client._get_cache_file_path(cache_key)

        # Write invalid JSON to cache file
        with open(cache_file, "w") as f:
            f.write("invalid json content")

        # Reading should return None and not raise exception
        result = client._read_from_cache(cache_key)
        assert result is None

    def test_cache_directory_creation(self):
        """Test that cache directory is created if it doesn't exist"""
        client = self._get_test_client()

        cache_dir = os.path.join(self.test_cwd, ".ddtrace_api_cache")

        # Remove directory if it exists
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)

        assert not os.path.exists(cache_dir)

        # Calling _get_cache_file_path should create the directory
        client._get_cache_file_path("test_key")

        assert os.path.exists(cache_dir)
        assert os.path.isdir(cache_dir)

    def test_cache_works_across_multiple_client_instances(self):
        """Test that cache is shared across different client instances in same directory"""
        # Mock API response
        api_response = _get_setting_api_response()
        assert isinstance(api_response.body, str)
        expected_parsed_response = json.loads(api_response.body)

        metric_names = APIRequestMetricNames(
            count="test.count", duration="test.duration", response_bytes=None, error="test.error"
        )

        method = "POST"
        endpoint = "/api/v2/libraries/tests/services/setting"
        payload = '{"data": {"test": "payload"}}'

        # First client makes request
        client1 = self._get_test_client()
        with patch.object(client1, "_do_request", return_value=api_response) as mock_do_request1:
            result1 = client1._do_request_with_telemetry(method, endpoint, payload, metric_names)
            assert result1 == expected_parsed_response
            # Verify HTTP call was made by first client
            mock_do_request1.assert_called_once()

        # Second client instance should use cached data
        client2 = self._get_test_client()
        with patch.object(client2, "_do_request", return_value=api_response) as mock_do_request2:
            result2 = client2._do_request_with_telemetry(method, endpoint, payload, metric_names)

            # No HTTP call should be made by second client (uses cache)
            mock_do_request2.assert_not_called()

            # Results should be identical
            assert result2 == expected_parsed_response
            assert result1 == result2

    def test_cache_key_deterministic_across_instances(self):
        """Test that cache keys are deterministic across different client instances"""
        client1 = self._get_test_client()
        client2 = self._get_test_client()

        method = "POST"
        endpoint = "/api/v2/test/endpoint"
        payload = '{"same": "payload"}'

        key1 = client1._get_cache_key(method, endpoint, payload)
        key2 = client2._get_cache_key(method, endpoint, payload)

        assert key1 == key2
