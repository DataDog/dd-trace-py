from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace._trace.span import Span
from ddtrace.appsec._api_security.api_manager import APIManager
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from tests.utils import override_global_config


class TestApiSecurityManager:
    @pytest.fixture
    def api_manager(self):
        with override_global_config(
            values=dict(
                _asm_enabled=True,
                _api_security_enabled=True,
                _apm_tracing_enabled=True,
                _api_security_parse_response_body=True,
            )
        ):
            manager = APIManager()
            manager._appsec_processor = MagicMock()
            manager._asm_context = MagicMock()
            manager._metrics = MagicMock()
            manager._should_collect_schema = MagicMock(return_value=True)

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
        env.blocked = None
        return env

    def test_schema_callback_no_span(self, api_manager, tracer):
        """Test that _schema_callback exits early when environment has no span.
        Expects that _should_collect_schema is not called.
        """
        env = MagicMock()
        env.span = None

        api_manager._schema_callback(env)
        api_manager._should_collect_schema.assert_not_called()

    def test_schema_callback_feature_not_active(self, api_manager):
        """Test that _schema_callback exits early when API security feature is not active.
        Expects that _should_collect_schema is not called.
        """
        with override_global_config(values=dict(_api_security_enabled=False)):
            env = MagicMock()
            env.span = MagicMock(spec=Span)

            api_manager._schema_callback(env)
            api_manager._should_collect_schema.assert_not_called()

    def test_schema_callback_already_collected(self, api_manager, mock_environment):
        """Test that _schema_callback exits early when schema data is already collected in the span.
        Expects that _should_collect_schema is not called.
        """
        root_span = mock_environment.span._local_root
        root_span._meta = {api_manager.COLLECTED[0][1]: "some_value"}

        api_manager._schema_callback(mock_environment)
        api_manager._should_collect_schema.assert_not_called()

    @pytest.mark.parametrize("sampling_priority", [USER_REJECT, AUTO_REJECT])
    def test_schema_callback_sampling_priority_reject(self, api_manager, mock_environment, sampling_priority):
        """Test that _schema_callback doesn't collect schema when sampling priority indicates rejection.
        Expects that _should_collect_schema is called but call_waf_callback is not called.
        """
        root_span = mock_environment.span._local_root
        root_span.context.sampling_priority = sampling_priority

        api_manager._should_collect_schema.return_value = False
        api_manager._schema_callback(mock_environment)

        api_manager._should_collect_schema.assert_called_once()
        api_manager._asm_context.call_waf_callback.assert_not_called()

    @pytest.mark.parametrize("sampling_priority", [USER_KEEP, AUTO_KEEP])
    def test_schema_callback_sampling_priority_keep(self, api_manager, mock_environment, sampling_priority):
        """Test that _schema_callback properly processes schemas when sampling priority indicates keep.
        Expects schema collection to occur and metadata to be added to the root span.
        """
        root_span = mock_environment.span._local_root
        root_span.context.sampling_priority = sampling_priority

        mock_waf_result = MagicMock()
        mock_waf_result.api_security = {"_dd.appsec.s.req.body": {"type": "object"}}
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        mock_environment.waf_addresses = {
            SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES: {"Content-Type": "application/json"},
            SPAN_DATA_NAMES.REQUEST_BODY: {"key": "value"},
        }

        api_manager._schema_callback(mock_environment)

        api_manager._should_collect_schema.assert_called_once()
        api_manager._asm_context.call_waf_callback.assert_called_once()
        api_manager._metrics._report_api_security.assert_called_with(True, 1)

        assert len(root_span._meta) == 1
        assert "_dd.appsec.s.req.body" in root_span._meta

    @pytest.mark.parametrize("should_collect_return", [True, False, None])
    @pytest.mark.parametrize("sampling_priority", [USER_REJECT, AUTO_REJECT, AUTO_KEEP, USER_KEEP])
    def test_schema_callback_apm_tracing_disabled(
        self, api_manager, mock_environment, should_collect_return, sampling_priority
    ):
        """Test that _schema_callback manually keeps spans when APM tracing is disabled.
        Expects _asm_manual_keep to be called only when should_collect_schema returns True.
        """
        mock_waf_result = MagicMock()
        mock_waf_result.api_security = {
            "_dd.appsec.s.req.body": {"type": "object", "properties": {"name": {"type": "string"}}},
            "_dd.appsec.s.req.headers": {"type": "object"},
            "_dd.appsec.s.req.cookies": {"type": "object"},
            "_dd.appsec.s.req.query": {"type": "object"},
            "_dd.appsec.s.req.path_params": {"type": "object"},
            "_dd.appsec.s.res.headers": {"type": "object"},
            "_dd.appsec.s.res.body": {"type": "object", "properties": {"status": {"type": "string"}}},
        }
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        api_manager._should_collect_schema.return_value = should_collect_return
        mock_environment.span._local_root.context.sampling_priority = sampling_priority

        with override_global_config(values=dict(_apm_tracing_enabled=False)):
            with patch("ddtrace.appsec._api_security.api_manager._asm_manual_keep") as mock_keep:
                api_manager._schema_callback(mock_environment)

                # Verify manual keep was called only if should_collect_schema returns True
                if should_collect_return:
                    mock_keep.assert_called_once_with(mock_environment.span._local_root)
                else:
                    mock_keep.assert_not_called()

    def test_schema_callback_with_valid_waf_addresses(self, api_manager, mock_environment):
        """Test successful schema collection with valid WAF addresses for both request and response.
        Expects schemas to be processed and added to span metadata, with correct metrics reporting.
        """
        mock_waf_result = MagicMock()
        mock_waf_result.api_security = {
            "_dd.appsec.s.req.body": {"type": "object", "properties": {"name": {"type": "string"}}},
            "_dd.appsec.s.req.headers": {"type": "object"},
            "_dd.appsec.s.req.cookies": {"type": "object"},
            "_dd.appsec.s.req.query": {"type": "object"},
            "_dd.appsec.s.req.path_params": {"type": "object"},
            "_dd.appsec.s.res.headers": {"type": "object"},
            "_dd.appsec.s.res.body": {"type": "object", "properties": {"status": {"type": "string"}}},
        }
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        mock_environment.waf_addresses = {
            SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES: {"Content-Type": "application/json"},
            SPAN_DATA_NAMES.REQUEST_COOKIES: {"session": "abc123"},
            SPAN_DATA_NAMES.REQUEST_QUERY: {"search": "test"},
            SPAN_DATA_NAMES.REQUEST_PATH_PARAMS: {"id": "12345"},
            SPAN_DATA_NAMES.REQUEST_BODY: {"name": "test"},
            SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES: {"Content-Type": "application/json"},
            SPAN_DATA_NAMES.RESPONSE_BODY: lambda: {"status": "success"},
        }

        api_manager._schema_callback(mock_environment)

        api_manager._should_collect_schema.assert_called_once()
        api_manager._asm_context.call_waf_callback.assert_called_once()

        # Verify WAF payload includes all addresses
        call_args = api_manager._asm_context.call_waf_callback.call_args[0][0]
        for call_arg in [
            "PROCESSOR_SETTINGS",
            "REQUEST_HEADERS_NO_COOKIES",
            "REQUEST_COOKIES",
            "REQUEST_QUERY",
            "REQUEST_PATH_PARAMS",
            "REQUEST_BODY",
            "RESPONSE_HEADERS_NO_COOKIES",
            "RESPONSE_BODY",
        ]:
            assert call_arg in call_args

        root_span = mock_environment.span._local_root
        # Verify all schemas are stored in span metadata
        assert len(root_span._meta) == 7
        for meta in [
            "_dd.appsec.s.req.body",
            "_dd.appsec.s.req.headers",
            "_dd.appsec.s.req.cookies",
            "_dd.appsec.s.req.query",
            "_dd.appsec.s.req.path_params",
            "_dd.appsec.s.res.headers",
            "_dd.appsec.s.res.body",
        ]:
            assert meta in root_span._meta

        api_manager._metrics._report_api_security.assert_called_with(True, 7)

    def test_schema_callback_oversized_schema(self, api_manager, mock_environment):
        """Test that _schema_callback handles oversized schemas correctly.
        Expects that an exception is caught when schema size exceeds limits and no schemas are reported.
        """
        mock_waf_result = MagicMock()
        large_schema = {"type": "object", "properties": {f"prop_{i}": {"type": "string"} for i in range(10000)}}
        mock_waf_result.api_security = {"_dd.appsec.s.req.body": large_schema}
        api_manager._asm_context.call_waf_callback.return_value = mock_waf_result

        with patch("gzip.compress") as mock_compress:
            mock_compress.return_value = b"x" * 100000  # data exceeding MAX_SPAN_META_VALUE_LEN
            mock_environment.waf_addresses = {SPAN_DATA_NAMES.REQUEST_BODY: {"name": "test"}}

            with patch("ddtrace.appsec._api_security.api_manager.log") as mock_log:
                api_manager._schema_callback(mock_environment)

            mock_log.warning.assert_called_once()
            root_span = mock_environment.span._local_root
            assert len(root_span._meta) == 0
            api_manager._metrics._report_api_security.assert_called_with(True, 0)

    def test_schema_callback_parse_response_body_disabled(self, api_manager, mock_environment, caplog):
        """Test that _schema_callback respects the api_security_parse_response_body configuration.
        Expects that when disabled, response body is not included in WAF payload and response schema is not collected.
        """

        with override_global_config(values=dict(_api_security_parse_response_body=False)):
            mock_waf_result = MagicMock()
            api_manager._asm_context.call_waf_callback.return_value = mock_waf_result
            mock_environment.waf_addresses = {
                SPAN_DATA_NAMES.RESPONSE_BODY: {"status": "success"},  # This should be ignored
            }

            api_manager._schema_callback(mock_environment)

            call_args = api_manager._asm_context.call_waf_callback.call_args[0][0]
            assert "RESPONSE_BODY" not in call_args

            assert len(mock_environment.span._local_root._meta) == 0
            api_manager._metrics._report_api_security.assert_called_with(True, 0)
