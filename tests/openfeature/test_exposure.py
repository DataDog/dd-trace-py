"""
Tests for exposure event building.
"""

from openfeature.evaluation_context import EvaluationContext

from ddtrace.internal.openfeature._exposure import _build_subject
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
        assert "timestamp" in event
        assert isinstance(event["timestamp"], int)

    def test_build_exposure_event_without_allocation_key(self):
        """Test that variant_key is used as allocation_key if not provided."""
        context = EvaluationContext(targeting_key="user-456")

        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-b",
            allocation_key=None,
            evaluation_context=context,
        )

        assert event is not None
        assert event["allocation"]["key"] == "variant-b"
        assert event["variant"]["key"] == "variant-b"

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

        assert event is not None
        assert event["variant"]["key"] == ""
        assert event["allocation"]["key"] == "allocation-1"

    def test_build_exposure_event_missing_subject(self):
        """Test that None is returned when subject cannot be built."""
        event = build_exposure_event(
            flag_key="test-flag",
            variant_key="variant-a",
            allocation_key="allocation-1",
            evaluation_context=None,
        )

        assert event is None


class TestBuildSubject:
    """Test subject building from evaluation context."""

    def test_build_subject_with_targeting_key(self):
        """Test building subject with targeting_key."""
        context = EvaluationContext(targeting_key="user-123")

        subject = _build_subject(context)

        assert subject is not None
        assert subject["id"] == "user-123"

    def test_build_subject_with_attributes(self):
        """Test building subject with attributes."""
        context = EvaluationContext(targeting_key="user-123", attributes={"tier": "premium", "region": "us-east"})

        subject = _build_subject(context)

        assert subject is not None
        assert subject["id"] == "user-123"
        assert subject["attributes"]["tier"] == "premium"
        assert subject["attributes"]["region"] == "us-east"

    def test_build_subject_with_type(self):
        """Test building subject with explicit subject_type."""
        context = EvaluationContext(
            targeting_key="org-456", attributes={"subject_type": "organization", "name": "ACME Corp"}
        )

        subject = _build_subject(context)

        assert subject is not None
        assert subject["id"] == "org-456"
        assert subject["type"] == "organization"
        assert subject["attributes"]["name"] == "ACME Corp"
        # subject_type should not appear in attributes
        assert "subject_type" not in subject["attributes"]

    def test_build_subject_missing_targeting_key(self):
        """Test that None is returned when targeting_key is missing."""
        context = EvaluationContext(targeting_key=None, attributes={"tier": "premium"})

        subject = _build_subject(context)

        assert subject is None

    def test_build_subject_none_context(self):
        """Test that None is returned when context is None."""
        subject = _build_subject(None)

        assert subject is None

    def test_build_subject_empty_attributes(self):
        """Test building subject with no attributes."""
        context = EvaluationContext(targeting_key="user-789")

        subject = _build_subject(context)

        assert subject is not None
        assert subject["id"] == "user-789"
        assert "attributes" not in subject
        assert "type" not in subject
