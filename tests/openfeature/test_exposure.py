"""
Tests for exposure event building.
"""

from openfeature.evaluation_context import EvaluationContext

from ddtrace.internal.openfeature._exposure import build_exposure_event


class TestBuildExposureEvent:
    """Test exposure event building."""

    def test_build_complete_exposure_event(self):
        """Test building a complete exposure event with all fields."""
        context = EvaluationContext(
            targeting_key="user-123", attributes={"tier": "premium", "email": "test@example.com"}
        )

        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-a",
            allocation_key="allocation-1",
            evaluation_context=context,
        )

        assert event is not None
        assert event["flag"]["key"] == "test-flag"
        assert event["variant"]["key"] == "variant-a"
        assert event["allocation"]["key"] == "allocation-1"
        assert event["subject"]["id"] == "user-123"
        assert event["subject"]["attributes"] == {"tier": "premium", "email": "test@example.com"}
        assert "timestamp" in event
        assert isinstance(event["timestamp"], int)

    def test_build_exposure_event_missing_allocation_key(self):
        """Test that None is returned when allocation_key is not provided."""
        context = EvaluationContext(targeting_key="user-456")

        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-b",
            allocation_key=None,
            evaluation_context=context,
        )

        assert event is None

    def test_build_exposure_event_missing_flag_key(self):
        """Test that None is returned when flag_key is missing."""
        context = EvaluationContext(targeting_key="user-123")

        event = build_exposure_event(
            flag_key="",
            variant_key="variant-a",
            allocation_key="allocation-1",
            evaluation_context=context,
        )

        assert event is None

    def test_build_exposure_event_missing_variant_key(self):
        """Test that None is returned when variant_key is missing."""
        context = EvaluationContext(targeting_key="user-123")

        event = build_exposure_event(
            flag_key="test-flag",
            variant_key=None,
            allocation_key="allocation-1",
            evaluation_context=context,
        )

        assert event is None

    def test_build_exposure_event_none_context(self):
        """Test that exposure event is built with empty targeting_key when context is None."""
        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-a",
            allocation_key="allocation-1",
            evaluation_context=None,
        )

        assert event is not None
        assert event["subject"]["id"] == ""
        assert event["subject"]["attributes"] == {}

    def test_build_exposure_event_missing_targeting_key(self):
        """Test that exposure event uses empty string when targeting_key is missing."""
        context = EvaluationContext(targeting_key=None, attributes={"tier": "premium"})

        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-a",
            allocation_key="allocation-1",
            evaluation_context=context,
        )

        assert event is not None
        assert event["subject"]["id"] == ""
        assert event["subject"]["attributes"] == {"tier": "premium"}

    def test_build_exposure_event_empty_attributes(self):
        """Test building exposure event with no attributes in context."""
        context = EvaluationContext(targeting_key="user-789")

        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-a",
            allocation_key="allocation-1",
            evaluation_context=context,
        )

        assert event is not None
        assert event["subject"]["id"] == "user-789"
        assert event["subject"]["attributes"] == {}
