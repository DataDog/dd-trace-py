import json
import os
import shutil
import tempfile
from unittest.mock import patch

import pytest

from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility._api_client import _CONFIGURATIONS_TYPE
from ddtrace.internal.ci_visibility._api_client import AgentlessTestVisibilityAPIClient
from ddtrace.internal.ci_visibility._api_responses_cache import _API_RESPONSE_CACHE_DIR
from ddtrace.internal.ci_visibility._api_responses_cache import _get_cache_file_path
from ddtrace.internal.ci_visibility._api_responses_cache import _get_normalized_cache_key
from ddtrace.internal.ci_visibility._api_responses_cache import _read_from_cache
from ddtrace.internal.ci_visibility._api_responses_cache import _write_to_cache
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
        method = "POST"
        endpoint = "/api/v2/libraries/tests/services/setting"
        payload = {"data": {"id": "some-uuid", "type": "test", "attributes": {"test": "value"}}}

        # Generate cache key using normalized method (ignores UUID)
        cache_key1 = _get_normalized_cache_key(method, endpoint, payload)
        cache_key2 = _get_normalized_cache_key(method, endpoint, payload)

        # Same parameters should generate same cache key
        assert cache_key1 == cache_key2
        assert len(cache_key1) == 64  # SHA256 hex digest length

        # Different UUID should generate SAME cache key (normalization working)
        payload_different_uuid = {"data": {"id": "different-uuid", "type": "test", "attributes": {"test": "value"}}}
        cache_key3 = _get_normalized_cache_key(method, endpoint, payload_different_uuid)
        assert cache_key1 == cache_key3  # Should be same due to normalization

        # Different attributes should generate different cache key
        payload_different_attrs = {
            "data": {"id": "some-uuid", "type": "test", "attributes": {"test": "different_value"}}
        }
        cache_key4 = _get_normalized_cache_key(method, endpoint, payload_different_attrs)
        assert cache_key1 != cache_key4

    def test_cache_key_includes_all_parameters(self):
        """Test that cache key changes when any parameter changes"""
        base_method = "POST"
        base_endpoint = "/api/v2/libraries/tests/services/setting"
        base_payload = {"data": {"id": "uuid", "type": "test", "attributes": {"test": "value"}}}

        base_key = _get_normalized_cache_key(base_method, base_endpoint, base_payload)

        # Different method
        method_key = _get_normalized_cache_key("GET", base_endpoint, base_payload)
        assert base_key != method_key

        # Different endpoint
        endpoint_key = _get_normalized_cache_key(base_method, "/different/endpoint", base_payload)
        assert base_key != endpoint_key

        # Different payload attributes (meaningful change)
        different_payload = {"data": {"id": "uuid", "type": "different_type", "attributes": {"test": "value"}}}
        payload_key = _get_normalized_cache_key(base_method, base_endpoint, different_payload)
        assert base_key != payload_key

    def test_cache_file_path_generation(self):
        """Test that cache file paths are generated correctly"""

        cache_key = "abc123def456"
        cache_path = _get_cache_file_path(cache_key)

        expected_dir = _API_RESPONSE_CACHE_DIR
        expected_path = os.path.join(expected_dir, f"{cache_key}.json")

        assert cache_path == expected_path
        # Directory should be created
        assert os.path.exists(expected_dir)

    def test_cache_write_and_read(self):
        """Test that data can be written to and read from cache"""
        cache_key = "test_cache_key"
        test_data = {"result": "cached_response", "status": "success"}

        with patch("ddtrace.internal.ci_visibility._api_responses_cache._is_response_cache_enabled", return_value=True):
            # Write to cache
            _write_to_cache(cache_key, test_data)

            # Read from cache
            cached_data = _read_from_cache(cache_key)

            assert cached_data == test_data

    def test_cache_miss_returns_none(self):
        """Test that cache miss returns empty dict"""
        non_existent_key = "this_key_does_not_exist"
        cached_data = _read_from_cache(non_existent_key)

        assert cached_data is None

    def test_first_request_makes_http_call_and_caches_response(self):
        """Test that the first request makes an HTTP call and caches the response"""
        client = self._get_test_client()

        # Mock API response
        api_response: Response = _get_setting_api_response()
        assert isinstance(api_response.body, str)
        expected_parsed_response = json.loads(api_response.body)

        # Mock _do_request to return the response without making actual HTTP call
        with patch(
            "ddtrace.internal.ci_visibility._api_responses_cache._is_response_cache_enabled", return_value=True
        ), patch.object(client, "_do_request", return_value=api_response) as mock_do_request:
            metric_names = APIRequestMetricNames(
                count="test.count", duration="test.duration", response_bytes=None, error="test.error"
            )

            method = "POST"
            endpoint = "/api/v2/libraries/tests/services/setting"
            # Use realistic dict payload with UUID (like real API calls)
            payload = {
                "data": {
                    "id": "some-uuid-1234",
                    "type": "ci_app_test_service_libraries_settings",
                    "attributes": {
                        "service": "test_service",
                        "env": "test_env",
                        "repository_url": "git@github.com:test/repo.git",
                        "sha": "abc123",
                        "configurations": {"os": "linux"},
                    },
                }
            }

            # First request should make HTTP call
            result = client._do_request_with_telemetry(method, endpoint, payload, metric_names, read_from_cache=False)

            # Verify HTTP call was made with JSON string
            mock_do_request.assert_called_once_with(method, endpoint, json.dumps(payload), timeout=None)
            assert result == expected_parsed_response

            # Verify response was cached using normalized key
            cache_key = _get_normalized_cache_key(method, endpoint, payload)
            cached_data = _read_from_cache(cache_key)
            assert cached_data == expected_parsed_response

    def test_second_request_uses_cached_response(self):
        """Test that the second identical request uses cached response without HTTP call"""
        client = self._get_test_client()

        # Mock API response for first call
        api_response = _get_setting_api_response()
        assert isinstance(api_response.body, str)
        expected_parsed_response = json.loads(api_response.body)

        # Mock _do_request to return the response without making actual HTTP call
        with patch(
            "ddtrace.internal.ci_visibility._api_responses_cache._is_response_cache_enabled", return_value=True
        ), patch.object(client, "_do_request", return_value=api_response) as mock_do_request:
            metric_names = APIRequestMetricNames(
                count="test.count", duration="test.duration", response_bytes=None, error="test.error"
            )

            method = "POST"
            endpoint = "/api/v2/libraries/tests/services/setting"
            # Use realistic payload that would normally have different UUIDs
            payload1 = {
                "data": {
                    "id": "uuid-first-request",
                    "type": "ci_app_test_service_libraries_settings",
                    "attributes": {
                        "service": "test_service",
                        "env": "test_env",
                        "repository_url": "git@github.com:test/repo.git",
                        "sha": "abc123",
                        "configurations": {"os": "linux"},
                    },
                }
            }

            payload2 = {
                "data": {
                    "id": "uuid-second-request",  # Different UUID!
                    "type": "ci_app_test_service_libraries_settings",
                    "attributes": {
                        "service": "test_service",
                        "env": "test_env",
                        "repository_url": "git@github.com:test/repo.git",
                        "sha": "abc123",
                        "configurations": {"os": "linux"},
                    },
                }
            }

            # First request
            result1 = client._do_request_with_telemetry(method, endpoint, payload1, metric_names, read_from_cache=False)
            assert result1 == expected_parsed_response

            # Reset mock to track second call
            mock_do_request.reset_mock()

            # Second request with different UUID but same attributes should use cache
            result2 = client._do_request_with_telemetry(method, endpoint, payload2, metric_names)

            # Verify no HTTP call was made for second request (cache hit due to normalization)
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
        with patch(
            "ddtrace.internal.ci_visibility._api_responses_cache._is_response_cache_enabled", return_value=True
        ), patch.object(client, "_do_request", side_effect=[response1, response2]) as mock_do_request:
            metric_names = APIRequestMetricNames(
                count="test.count", duration="test.duration", response_bytes=None, error="test.error"
            )

            method = "POST"
            endpoint = "/api/v2/libraries/tests/services/setting"
            payload1 = {"data": {"attributes": "payload1"}}
            payload2 = {"data": {"attributes": "payload2"}}

            # First request
            result1 = client._do_request_with_telemetry(method, endpoint, payload1, metric_names, read_from_cache=False)
            assert result1 == expected_parsed_response1

            # Second request with different payload should make HTTP call
            result2 = client._do_request_with_telemetry(method, endpoint, payload2, metric_names)
            assert result2 == expected_parsed_response2

            # Verify both HTTP calls were made (different payloads)
            assert mock_do_request.call_count == 2

            # Verify both are cached separately
            cache_key1 = _get_normalized_cache_key(method, endpoint, payload1)
            cache_key2 = _get_normalized_cache_key(method, endpoint, payload2)

            assert _read_from_cache(cache_key1) == expected_parsed_response1
            assert _read_from_cache(cache_key2) == expected_parsed_response2

    def test_cache_handles_invalid_json_gracefully(self):
        """Test that cache operations handle invalid JSON gracefully"""
        cache_key = "test_invalid_json"
        cache_file = _get_cache_file_path(cache_key)

        # Write invalid JSON to cache file
        with open(cache_file, "w") as f:
            f.write("invalid json content")

        # Reading should return None and not raise exception
        result = _read_from_cache(cache_key)
        assert result is None

    def test_cache_directory_creation(self):
        """Test that cache directory is created if it doesn't exist"""

        # Remove directory if it exists
        if os.path.exists(_API_RESPONSE_CACHE_DIR):
            shutil.rmtree(_API_RESPONSE_CACHE_DIR)

        assert not os.path.exists(_API_RESPONSE_CACHE_DIR)

        # client = self._get_test_client()
        # Calling _get_cache_file_path should create the directory
        _get_cache_file_path("test_key")

        assert os.path.exists(_API_RESPONSE_CACHE_DIR)
        assert os.path.isdir(_API_RESPONSE_CACHE_DIR)

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
        payload = {"data": {"type": "type", "attributes": "payload"}}

        with patch("ddtrace.internal.ci_visibility._api_responses_cache._is_response_cache_enabled", return_value=True):
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
        method = "POST"
        endpoint = "/api/v2/test/endpoint"
        payload = {"data": {"id": "some-uuid", "type": "test", "attributes": {"same": "payload"}}}

        key1 = _get_normalized_cache_key(method, endpoint, payload)
        key2 = _get_normalized_cache_key(method, endpoint, payload)

        assert key1 == key2

    def test_uuid_normalization_enables_caching(self):
        """Test that requests with different UUIDs but same attributes are cached together"""

        method = "POST"
        endpoint = "/api/v2/libraries/tests/services/setting"

        # Two payloads with different UUIDs but same attributes
        payload1 = {
            "data": {
                "id": "uuid-1111-1111-1111",
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "service": "test_service",
                    "env": "test_env",
                    "repository_url": "git@github.com:test/repo.git",
                    "sha": "abc123",
                    "configurations": {"os": "linux"},
                },
            }
        }

        payload2 = {
            "data": {
                "id": "uuid-2222-2222-2222",  # Different UUID!
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "service": "test_service",
                    "env": "test_env",
                    "repository_url": "git@github.com:test/repo.git",
                    "sha": "abc123",
                    "configurations": {"os": "linux"},
                },
            }
        }

        # Both should generate the same cache key despite different UUIDs
        cache_key1 = _get_normalized_cache_key(method, endpoint, payload1)
        cache_key2 = _get_normalized_cache_key(method, endpoint, payload2)
        assert cache_key1 == cache_key2, "UUID normalization should make these cache keys identical"

        # But different meaningful attributes should generate different keys
        payload3 = {
            "data": {
                "id": "uuid-3333-3333-3333",
                "type": "ci_app_test_service_libraries_settings",
                "attributes": {
                    "service": "different_service",  # Different attribute
                    "env": "test_env",
                    "repository_url": "git@github.com:test/repo.git",
                    "sha": "abc123",
                    "configurations": {"os": "linux"},
                },
            }
        }

        cache_key3 = _get_normalized_cache_key(method, endpoint, payload3)
        assert cache_key1 != cache_key3, "Different attributes should generate different cache keys"
