import gzip
from typing import List
from typing import Optional
from typing import Tuple
from unittest import mock

from ddtrace.internal._encoding import BufferedEncoder
from ddtrace.internal.writer import AgentWriter
from ddtrace.trace import Span
from tests.utils import BaseTestCase
from tests.utils import override_global_config


class MultiplePayloadEncoder(BufferedEncoder):
    """Custom encoder that returns multiple payloads."""

    content_type = "application/json"

    def __init__(self, max_size=8 << 20, max_item_size=1 << 20):
        super().__init__()  # Cython __cinit__ gets constructor args automatically
        self.payloads_data = [b"payload1", b"payload2", b"payload3"]
        self.n_traces_data = [1, 2, 3]
        self._traces = []

    def set_test_data(self, payloads_data=None, n_traces_data=None):
        """Configure test data for the encoder."""
        if payloads_data is not None:
            self.payloads_data = payloads_data
        if n_traces_data is not None:
            self.n_traces_data = n_traces_data
        return self

    def __len__(self):
        return sum(self.n_traces_data)

    @property
    def size(self):
        return len(self._traces)

    def put(self, item):
        self._traces.append(item)

    def encode(self) -> List[Tuple[Optional[bytes], int]]:
        """Return multiple payloads as the encoder change expects."""
        return list(zip(self.payloads_data, self.n_traces_data))

    def encode_traces(self, traces):
        # Not used in HTTPWriter but needed for interface compatibility
        return b"unused"


class SinglePayloadEncoder(BufferedEncoder):
    """Standard encoder that returns a single payload."""

    content_type = "application/json"

    def __init__(self, max_size=8 << 20, max_item_size=1 << 20):
        super().__init__()  # Cython __cinit__ gets constructor args automatically
        self.payload_data = b"single_payload"
        self.n_traces = 5
        self._traces = []

    def set_test_data(self, payload_data=None, n_traces=None):
        """Configure test data for the encoder."""
        if payload_data is not None:
            self.payload_data = payload_data
        if n_traces is not None:
            self.n_traces = n_traces
        return self

    def __len__(self):
        return self.n_traces

    @property
    def size(self):
        return len(self._traces)

    def put(self, item):
        self._traces.append(item)

    def encode(self) -> List[Tuple[Optional[bytes], int]]:
        """Return single payload."""
        return [(self.payload_data, self.n_traces)]

    def encode_traces(self, traces):
        return b"unused"


class NonePayloadEncoder(BufferedEncoder):
    """Encoder that returns None payloads."""

    content_type = "application/json"

    def __init__(self, max_size=8 << 20, max_item_size=1 << 20):
        super().__init__()  # Cython __cinit__ gets constructor args automatically
        self._traces = []
        self.n_traces = 2

    def __len__(self):
        return self.n_traces

    @property
    def size(self):
        return len(self._traces)

    def put(self, item):
        self._traces.append(item)

    def encode(self) -> List[Tuple[Optional[bytes], int]]:
        """Return payloads with None data."""
        return [(None, 1), (b"valid_payload", 1)]

    def encode_traces(self, traces):
        return b"unused"


class TestHTTPWriterMultiplePayloads(BaseTestCase):
    """Test HTTPWriter handling of multiple payloads from encoder."""

    def setUp(self):
        super().setUp()
        self.statsd = mock.Mock()

    def test_multiple_payloads_processed_individually(self):
        """Test that multiple payloads are each processed through _flush_single_payload."""
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = AgentWriter("http://localhost:8126", dogstatsd=self.statsd, sync_mode=True)

            # Replace the encoder with our custom multiple payload encoder
            encoder = MultiplePayloadEncoder(8 << 20, 1 << 20).set_test_data(
                payloads_data=[b"payload1", b"payload2", b"payload3"], n_traces_data=[1, 2, 3]
            )
            for client in writer._clients:
                client.encoder = encoder

            # Mock the _flush_single_payload method to track calls
            with mock.patch.object(writer, "_flush_single_payload") as mock_flush_single:
                # Write some traces
                writer.write([Span(name="test", trace_id=1, span_id=1)])
                # NOTE: No need to call flush_queue() because sync_mode=True auto-flushes

                # Verify _flush_single_payload was called once for each payload
                assert mock_flush_single.call_count == 3

                # Verify each call had the correct payload and trace count
                calls = mock_flush_single.call_args_list
                assert calls[0][0][0] == b"payload1"  # encoded data
                assert calls[0][0][1] == 1  # n_traces

                assert calls[1][0][0] == b"payload2"
                assert calls[1][0][1] == 2

                assert calls[2][0][0] == b"payload3"
                assert calls[2][0][1] == 3

    def test_single_payload_backward_compatibility(self):
        """Test that single payload encoders still work as before."""
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = AgentWriter("http://localhost:8126", dogstatsd=self.statsd, sync_mode=True)

            # Replace the encoder with a single payload encoder
            encoder = SinglePayloadEncoder(8 << 20, 1 << 20).set_test_data(payload_data=b"single_test", n_traces=5)
            for client in writer._clients:
                client.encoder = encoder

            # Mock the _flush_single_payload method to track calls
            with mock.patch.object(writer, "_flush_single_payload") as mock_flush_single:
                writer.write([Span(name="test", trace_id=1, span_id=1)])
                # NOTE: No need to call flush_queue() because sync_mode=True auto-flushes

                # Verify _flush_single_payload was called once
                assert mock_flush_single.call_count == 1

                # Verify the call had the correct payload and trace count
                call = mock_flush_single.call_args_list[0]
                assert call[0][0] == b"single_test"  # encoded data
                assert call[0][1] == 5  # n_traces

    def test_none_payloads_skipped(self):
        """Test that None payloads are skipped but valid ones are processed."""
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = AgentWriter("http://localhost:8126", dogstatsd=self.statsd, sync_mode=True)

            # Replace the encoder with a None payload encoder
            encoder = NonePayloadEncoder(8 << 20, 1 << 20)
            for client in writer._clients:
                client.encoder = encoder

            # Mock the _flush_single_payload method to track calls
            with mock.patch.object(writer, "_flush_single_payload") as mock_flush_single:
                writer.write([Span(name="test", trace_id=1, span_id=1)])
                # NOTE: No need to call flush_queue() because sync_mode=True auto-flushes

                # Verify _flush_single_payload was called twice (once for None, once for valid payload)
                assert mock_flush_single.call_count == 2

                # Verify the calls: first with None (should return early), second with valid payload
                calls = mock_flush_single.call_args_list
                assert calls[0][0][0] is None  # First call with None data
                assert calls[0][0][1] == 1  # n_traces for None payload
                assert calls[1][0][0] == b"valid_payload"  # Second call with valid data
                assert calls[1][0][1] == 1  # n_traces for valid payload

    def test_gzip_compression_per_payload(self):
        """Test that gzip compression is applied to each payload individually."""
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = AgentWriter("http://localhost:8126", dogstatsd=self.statsd, sync_mode=True)
            writer._intake_accepts_gzip = True  # Enable gzip compression

            # Replace the encoder with multiple payloads
            encoder = MultiplePayloadEncoder(8 << 20, 1 << 20).set_test_data(
                payloads_data=[b"test_payload_1", b"test_payload_2"], n_traces_data=[1, 1]
            )
            for client in writer._clients:
                client.encoder = encoder

            # Mock the _send_payload_with_backoff method to capture compressed payloads
            with mock.patch.object(writer, "_send_payload_with_backoff") as mock_send:
                writer.write([Span(name="test", trace_id=1, span_id=1)])
                # NOTE: No need to call flush_queue() because sync_mode=True auto-flushes

                # Verify both payloads were sent
                assert mock_send.call_count == 2

                # Verify each payload was gzipped
                sent_payload_1 = mock_send.call_args_list[0][0][0]  # First argument of first call
                sent_payload_2 = mock_send.call_args_list[1][0][0]  # First argument of second call

                # Decompress and verify
                assert gzip.decompress(sent_payload_1) == b"test_payload_1"
                assert gzip.decompress(sent_payload_2) == b"test_payload_2"

    def test_individual_payload_error_handling(self):
        """Test that errors in individual payload sending are handled properly."""
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = AgentWriter("http://localhost:8126", dogstatsd=self.statsd, sync_mode=True)

            # Replace the encoder with multiple payloads
            encoder = MultiplePayloadEncoder(8 << 20, 1 << 20).set_test_data(
                payloads_data=[b"payload1", b"payload2", b"payload3"], n_traces_data=[1, 2, 3]
            )
            for client in writer._clients:
                client.encoder = encoder

            # Mock _send_payload_with_backoff to fail on second payload
            def mock_send_side_effect(payload, n_traces, client):
                if payload == b"test_payload_2":  # This won't match due to gzip
                    raise Exception("Network error")

            with mock.patch.object(writer, "_send_payload_with_backoff", side_effect=Exception("Network error")):
                writer.write([Span(name="test", trace_id=1, span_id=1)])
                # NOTE: No need to call flush_queue() because sync_mode=True auto-flushes

                # Verify error metrics were recorded for each failed payload
                # Should be 3 calls to http.errors (one for each payload that failed)
                error_calls = [call for call in self.statsd.distribution.call_args_list if "http.errors" in str(call)]
                assert len(error_calls) == 3

    def test_metrics_recorded_per_payload(self):
        """Test that metrics are recorded correctly for each payload."""
        with override_global_config(dict(_health_metrics_enabled=True)):
            writer = AgentWriter("http://localhost:8126", dogstatsd=self.statsd, sync_mode=True)

            # Replace the encoder with multiple payloads
            encoder = MultiplePayloadEncoder(8 << 20, 1 << 20).set_test_data(
                payloads_data=[b"small", b"medium_payload", b"large_payload_data"], n_traces_data=[1, 2, 3]
            )
            for client in writer._clients:
                client.encoder = encoder

            # Mock successful sending
            with mock.patch.object(writer, "_send_payload_with_backoff") as mock_send:
                writer.write([Span(name="test", trace_id=1, span_id=1)])
                # NOTE: No need to call flush_queue() because sync_mode=True auto-flushes

                # Verify all payloads were sent
                assert mock_send.call_count == 3

                # Verify traces were counted correctly in metrics
                trace_calls = [call for call in self.statsd.distribution.call_args_list if "sent.traces" in str(call)]

                # Should have calls for 1, 2, and 3 traces respectively
                trace_counts = [call[0][1] for call in trace_calls]
                assert 1 in trace_counts
                assert 2 in trace_counts
                assert 3 in trace_counts
