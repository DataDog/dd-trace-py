"""
Tests for the ExposureWriter class.
"""

import json
from unittest import mock

import pytest

from ddtrace.internal.openfeature.writer import ExposureEvent
from ddtrace.internal.openfeature.writer import ExposureWriter
from ddtrace.internal.service import ServiceStatus
from tests.utils import override_global_config


@pytest.fixture
def writer():
    """Create an ExposureWriter instance for testing."""
    w = ExposureWriter(interval=0.1, timeout=2.0)
    w.start()
    yield w
    w.stop()


@pytest.fixture
def sample_exposure_event() -> ExposureEvent:
    """Create a sample exposure event for testing."""
    return {
        "timestamp": 1234567890000,
        "allocation": {"key": "allocation-1"},
        "flag": {"key": "test-flag"},
        "variant": {"key": "variant-a"},
        "subject": {"id": "user-123", "type": "user", "attributes": {"tier": "premium"}},
    }


class TestExposureWriter:
    """Test ExposureWriter functionality."""

    def test_writer_initialization(self):
        """Test writer initializes with correct configuration."""
        writer = ExposureWriter(interval=1.0, timeout=2.0)

        assert writer._timeout == 2.0
        assert writer._endpoint == "/evp_proxy/v2/api/v2/exposures"
        assert writer._headers["Content-Type"] == "application/json"
        assert "X-Datadog-EVP-Subdomain" in writer._headers

    def test_enqueue_event(self, writer, sample_exposure_event):
        """Test enqueueing an exposure event."""
        writer.enqueue(sample_exposure_event)

        assert len(writer._buffer) == 1
        assert writer._buffer[0] == sample_exposure_event

    def test_encode_events(self, writer, sample_exposure_event):
        """Test encoding events to JSON."""
        events = [sample_exposure_event]
        encoded = writer._encode(events)

        assert encoded is not None
        assert len(encoded) > 0

        # Verify it's valid JSON with batch structure
        decoded = json.loads(encoded)
        assert "exposures" in decoded
        assert len(decoded["exposures"]) == 1
        assert decoded["exposures"][0]["flag"]["key"] == "test-flag"

    def test_buffer_limit(self, writer, sample_exposure_event):
        """Test that buffer respects the limit."""
        # Enqueue more than buffer limit
        for i in range(1010):
            event = sample_exposure_event.copy()
            event["timestamp"] = 1234567890000 + i
            writer.enqueue(event)

        # Should not exceed buffer limit
        assert len(writer._buffer) <= 1000

    @mock.patch("ddtrace.internal.openfeature.writer.get_connection")
    def test_send_payload(self, mock_get_connection, writer, sample_exposure_event):
        """Test sending payload to EVP proxy."""
        # Setup mock connection
        mock_conn = mock.Mock()
        mock_resp = mock.Mock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"OK"
        mock_conn.getresponse.return_value = mock_resp
        mock_get_connection.return_value = mock_conn

        # Enqueue and flush
        writer.enqueue(sample_exposure_event)
        writer.periodic()

        # Verify connection was made
        mock_get_connection.assert_called()
        mock_conn.request.assert_called()

        # Verify request details
        call_args = mock_conn.request.call_args
        assert call_args[0][0] == "POST"
        assert "/evp_proxy/v2/api/v2/exposures" in call_args[0][1]
        assert call_args[0][3] == writer._headers

    def test_periodic_flushes_buffer(self, writer, sample_exposure_event):
        """Test that periodic() flushes the buffer."""
        writer.enqueue(sample_exposure_event)
        assert len(writer._buffer) == 1

        with mock.patch.object(writer, "_send_payload"):
            writer.periodic()

        # Buffer should be cleared
        assert len(writer._buffer) == 0

    def test_empty_buffer_no_send(self, writer):
        """Test that periodic() doesn't send when buffer is empty."""
        with mock.patch.object(writer, "_send_payload") as mock_send:
            writer.periodic()
            mock_send.assert_not_called()

    def test_writer_disabled_via_config(self):
        """Test that writer can be disabled via configuration."""
        with override_global_config({"ffe_intake_enabled": False}):
            writer = ExposureWriter()
            assert writer._enabled is False

            # Start should not actually start the service
            writer.start()
            assert writer._enabled is False
            assert writer.status != ServiceStatus.RUNNING

    def test_writer_custom_interval_via_config(self):
        """Test that interval can be configured via configuration."""
        with override_global_config({"ffe_intake_heartbeat_interval": 5.0}):
            writer = ExposureWriter()
            assert writer._interval == 5.0

    def test_writer_retry_mechanism(self, writer, sample_exposure_event):
        """Test that _send_payload_with_retry is set up correctly."""
        # Verify retry wrapper exists
        assert hasattr(writer, "_send_payload_with_retry")
        assert callable(writer._send_payload_with_retry)
