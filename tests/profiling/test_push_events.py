"""Tests for the push_event functionality."""
import pytest

from ddtrace.internal.datadog.profiling import ddup


class TestPushEvents:
    """Test suite for event pushing functionality."""

    def test_sample_handle_push_event(self) -> None:
        """Test that SampleHandle has push_event method."""
        handle = ddup.SampleHandle()
        # Should not raise
        handle.push_event("test_event")
        handle.flush_sample()

    def test_sample_handle_push_label(self) -> None:
        """Test that SampleHandle has push_label method."""
        handle = ddup.SampleHandle()
        # Should not raise
        handle.push_label("key", "value")
        handle.push_event("test_event")
        handle.flush_sample()

    def test_push_event_simple(self) -> None:
        """Test simple event push without stack trace."""
        # Should not raise
        ddup.push_event("test_event", capture_stack=False)

    def test_push_event_with_stack(self) -> None:
        """Test event push with stack trace."""
        # Should not raise
        ddup.push_event("test_event_with_stack", capture_stack=True)

    def test_push_event_with_labels(self) -> None:
        """Test event push with custom labels."""
        labels = {
            "label1": "value1",
            "label2": "value2",
            "label3": "value3",
        }
        # Should not raise
        ddup.push_event("test_event_with_labels", labels=labels, capture_stack=False)

    def test_push_event_with_limited_frames(self) -> None:
        """Test event push with limited number of frames."""
        # Should not raise
        ddup.push_event("test_event_limited", max_nframes=5)

    def test_push_event_all_options(self) -> None:
        """Test event push with all options."""
        labels = {"test": "label"}
        # Should not raise
        ddup.push_event(
            "test_event_full",
            labels=labels,
            capture_stack=True,
            max_nframes=10,
        )

    def test_manual_sample_with_event(self) -> None:
        """Test manually creating a sample with event type and labels."""
        handle = ddup.SampleHandle()
        
        # Push event type
        handle.push_event("manual_event")
        
        # Add custom labels
        handle.push_label("custom_label_1", "value1")
        handle.push_label("custom_label_2", "value2")
        
        # Add a frame
        handle.push_frame("test_function", "test_file.py", 0, 42)
        
        # Should not raise
        handle.flush_sample()

    def test_event_type_conversion(self) -> None:
        """Test that event types can be strings or bytes."""
        handle = ddup.SampleHandle()
        
        # String event type
        handle.push_event("string_event")
        handle.flush_sample()
        
        handle = ddup.SampleHandle()
        # Bytes event type
        handle.push_event(b"bytes_event")
        handle.flush_sample()

    def test_label_conversion(self) -> None:
        """Test that labels can be strings or bytes."""
        handle = ddup.SampleHandle()
        handle.push_event("test")
        
        # String labels
        handle.push_label("key_str", "val_str")
        
        # Bytes labels
        handle.push_label(b"key_bytes", b"val_bytes")
        
        handle.flush_sample()


@pytest.mark.skipif(not ddup.is_available, reason="ddup not available")
class TestPushEventsIntegration:
    """Integration tests requiring ddup to be initialized."""

    def test_push_event_after_init(self) -> None:
        """Test pushing events after ddup initialization."""
        # These tests assume ddup might be initialized in the test suite
        # Should not raise
        ddup.push_event("integration_test_event", capture_stack=False)

