import pytest
from unittest.mock import patch, MagicMock, ANY
from ddtrace.appsec._api_security.api_manager import APIManager
from ddtrace.constants import USER_KEEP, USER_REJECT, AUTO_KEEP, AUTO_REJECT
from ddtrace._trace.span import Span
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.sampling import SamplingMechanism
from ddtrace.appsec._constants import APPSEC


class TestApiSecurityManager:
    @pytest.fixture
    def api_manager(self):
        # Create an instance of APIManager with mocked dependencies
        with patch("ddtrace.appsec._api_security.api_manager.asm_config") as mock_config:
            mock_config._api_security_feature_active = True
            mock_config._apm_tracing_enabled = True
            mock_config._api_security_parse_response_body = True

            manager = APIManager()
            manager._appsec_processor = MagicMock()
            manager._asm_context = MagicMock()
            manager._metrics = MagicMock()
            manager._should_collect_schema = MagicMock(return_value=True)
            
            # Mock the logger module to prevent actual logging and allow testing log.warning calls
            with patch("ddtrace.appsec._api_security.api_manager.log") as mock_log:
                manager._log = mock_log
                yield manager

    @pytest.fixture
    def mock_environment(self):
        # Create a mock environment with required attributes
        env = MagicMock()
        root_span = MagicMock(spec=Span)
        root_span._meta = {}
        env.span = MagicMock(spec=Span)
        env.span._local_root = root_span
        env.span.context.sampling_priority = None
        root_span.context.sampling_priority = None
        env.waf_addresses = {}

        return env

    def test_schema_callback_no_span(self, api_manager):
        """Test when environment has no span."""
        env = MagicMock()
        env.span = None

        api_manager._schema_callback(env)

        # Verify no further processing
        api_manager._should_collect_schema.assert_not_called()

    def test_schema_callback_feature_not_active(self, api_manager):
        """Test when API security feature is not active."""
        with patch("ddtrace.appsec._api_security.api_manager.asm_config") as mock_config:
            mock_config._api_security_feature_active = False

            env = MagicMock()
            env.span = MagicMock()

            api_manager._schema_callback(env)

            # Verify no further processing
            api_manager._should_collect_schema.assert_not_called()

    def test_schema_callback_already_collected(self, api_manager, mock_environment):
        """Test when schema data is already collected."""
        # Set meta value for a collected item
        root_span = mock_environment.span._local_root
        root_span._meta = {api_manager.COLLECTED[0][1]: "some_value"}

        api_manager._schema_callback(mock_environment)

        # Verify no further processing
        api_manager._should_collect_schema.assert_not_called()

    @pytest.mark.parametrize("sampling_priority", [USER_REJECT, AUTO_REJECT])
    def test_schema_callback_sampling_priority_reject(self, api_manager, mock_environment, sampling_priority):
        """Test when sampling priority indicates request should be rejected."""
        root_span = mock_environment.span._local_root
        root_span.context.sampling_priority = sampling_priority

        api_manager._schema_callback(mock_environment)

        # Verify no further processing
        api_manager._should_collect_schema.assert_not_called()

    @pytest.mark.parametrize("sampling_priority", [USER_KEEP, AUTO_KEEP])
    def test_schema_callback_sampling_priority_keep(self, api_manager, mock_environment, sampling_priority):
        """Test when sampling priority is set to keep."""
        root_span = mock_environment.span._local_root
        root_span.context.sampling_priority = sampling_priority

        # Configure mock response
        mock_waf_result = MagicMock()
        mock_waf_result.derivatives = {"_dd.appsec.s.req.body": {"type": "object"}}
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        # Mock the WAF addresses
        mock_environment.waf_addresses = {
            SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES: {"Content-Type": "application/json"},
            SPAN_DATA_NAMES.REQUEST_BODY: {"key": "value"},
        }

        api_manager._schema_callback(mock_environment)

        # Verify processing occurred
        api_manager._should_collect_schema.assert_called_once()
        api_manager._asm_context.call_waf_callback.assert_called_once()
        api_manager._metrics._report_api_security.assert_called_with(True, 1)

        # Verify schema was added to span
        assert len(root_span._meta) == 1
        assert "_dd.appsec.s.req.body" in root_span._meta

        # Verify sampling priority was unchanged
        assert root_span.context.sampling_priority == sampling_priority

    @pytest.mark.parametrize("should_collect_return", [True, False, None])
    def test_schema_callback_apm_tracing_disabled(self, api_manager, mock_environment, should_collect_return):
        """Test when APM tracing is disabled."""

        # Configure _should_collect_schema to return the parameterized value
        api_manager._should_collect_schema.return_value = should_collect_return

        with patch("ddtrace.appsec._api_security.api_manager.asm_config") as mock_config:
            mock_config._api_security_feature_active = True
            mock_config._apm_tracing_enabled = False

            # Mock the _asm_manual_keep function
            with patch("ddtrace.appsec._api_security.api_manager._asm_manual_keep") as mock_keep:
                api_manager._schema_callback(mock_environment)

                # Verify manual keep was called only if should_collect_schema returns True
                if should_collect_return:
                    mock_keep.assert_called_once_with(mock_environment.span._local_root)
                else:
                    mock_keep.assert_not_called()

    def test_schema_callback_with_valid_waf_addresses(self, api_manager, mock_environment):
        """Test successful schema collection with valid WAF addresses."""
        # Configure mock response
        mock_waf_result = MagicMock()
        mock_waf_result.derivatives = {
            "_dd.appsec.s.req.body": {"type": "object", "properties": {"name": {"type": "string"}}},
            "_dd.appsec.s.res.body": {"type": "object", "properties": {"status": {"type": "string"}}},
        }
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        # Mock the WAF addresses
        mock_environment.waf_addresses = {
            SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES: {"Content-Type": "application/json"},
            SPAN_DATA_NAMES.REQUEST_BODY: {"name": "test"},
            SPAN_DATA_NAMES.RESPONSE_BODY: lambda: {"status": "success"},  # Wrap in a lambda
        }

        api_manager._schema_callback(mock_environment)

        # Verify processing occurred
        api_manager._should_collect_schema.assert_called_once()
        api_manager._asm_context.call_waf_callback.assert_called_once()

        # Verify both schemas were added to span
        root_span = mock_environment.span._local_root
        assert len(root_span._meta) == 2
        assert "_dd.appsec.s.req.body" in root_span._meta
        assert "_dd.appsec.s.res.body" in root_span._meta

        # Verify metrics
        api_manager._metrics._report_api_security.assert_called_with(True, 2)

    def test_schema_callback_oversized_schema(self, api_manager, mock_environment):
        """Test when schema is too large."""
        # Configure mock response with an extremely large schema
        mock_waf_result = MagicMock()

        # Create a schema that will be too large when compressed and base64 encoded
        large_schema = {"type": "object", "properties": {f"prop_{i}": {"type": "string"} for i in range(10000)}}
        mock_waf_result.derivatives = {"_dd.appsec.s.req.body": large_schema}
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        # Create a patch for gzip.compress to simulate large output
        with patch("gzip.compress") as mock_compress:
            # Return data that when encoded will exceed MAX_SPAN_META_VALUE_LEN
            mock_compress.return_value = b"x" * 100000

            # Mock the WAF addresses
            mock_environment.waf_addresses = {SPAN_DATA_NAMES.REQUEST_BODY: {"name": "test"}}

            with patch("ddtrace.appsec._api_security.api_manager.log") as mock_log:
                api_manager._schema_callback(mock_environment)

                mock_log.warning.assert_called_once()

            # Verify metrics
            api_manager._metrics._report_api_security.assert_called_with(True, 0)

    def test_schema_callback_parse_response_body_disabled(self, api_manager, mock_environment):
        """Test when API security parse response body is disabled."""
        with patch("ddtrace.appsec._api_security.api_manager.asm_config") as mock_config:
            mock_config._api_security_feature_active = True
            mock_config._apm_tracing_enabled = True
            mock_config._api_security_parse_response_body = False

            # Configure mock response
            mock_waf_result = MagicMock()
            mock_waf_result.derivatives = {"_dd.appsec.s.req.body": {"type": "object"}}
            api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

            # Set up WAF addresses
            mock_environment.waf_addresses = {
                SPAN_DATA_NAMES.REQUEST_BODY: {"name": "test"},
                SPAN_DATA_NAMES.RESPONSE_BODY: {"status": "success"},  # This should be ignored
            }

            api_manager._schema_callback(mock_environment)

            # Verify WAF call payload doesn't include RESPONSE_BODY
            call_args = api_manager._asm_context.call_waf_callback.call_args[0][0]
            assert "RESPONSE_BODY" not in call_args
