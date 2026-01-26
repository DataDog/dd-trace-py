"""
Tests for library capabilities related to coverage report upload.
"""

from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_coverage_report_upload
from ddtrace.internal.test_visibility._library_capabilities import LibraryCapabilities


class TestLibraryCapabilities:
    """Tests for LibraryCapabilities class."""

    def test_library_capabilities_coverage_report_upload(self) -> None:
        """Test that coverage_report_upload capability is correctly added to tags."""
        capabilities = LibraryCapabilities(
            coverage_report_upload="1",
            test_impact_analysis="1",
        )

        tags = capabilities.tags()

        assert "_dd.library_capabilities.coverage_report_upload" in tags
        assert tags["_dd.library_capabilities.coverage_report_upload"] == "1"
        assert "_dd.library_capabilities.test_impact_analysis" in tags
        assert tags["_dd.library_capabilities.test_impact_analysis"] == "1"

    def test_library_capabilities_coverage_report_upload_none(self) -> None:
        """Test that coverage_report_upload capability is not added when None."""
        capabilities = LibraryCapabilities(
            coverage_report_upload=None,
            test_impact_analysis="1",
        )

        tags = capabilities.tags()

        assert "_dd.library_capabilities.coverage_report_upload" not in tags
        assert "_dd.library_capabilities.test_impact_analysis" in tags
        assert tags["_dd.library_capabilities.test_impact_analysis"] == "1"

    def test_library_capabilities_all_supported_capabilities(self) -> None:
        """Test all supported library capabilities."""
        capabilities = LibraryCapabilities(
            early_flake_detection="1",
            auto_test_retries="1",
            test_impact_analysis="1",
            test_management_quarantine="1",
            test_management_disable="1",
            test_management_attempt_to_fix="5",
            coverage_report_upload="1",
        )

        tags = capabilities.tags()

        expected_tags = {
            "_dd.library_capabilities.early_flake_detection": "1",
            "_dd.library_capabilities.auto_test_retries": "1",
            "_dd.library_capabilities.test_impact_analysis": "1",
            "_dd.library_capabilities.test_management.quarantine": "1",
            "_dd.library_capabilities.test_management.disable": "1",
            "_dd.library_capabilities.test_management.attempt_to_fix": "5",
            "_dd.library_capabilities.coverage_report_upload": "1",
        }

        assert tags == expected_tags

    def test_pytest_version_supports_coverage_report_upload(self) -> None:
        """Test that coverage report upload is supported for all pytest versions."""
        # Since coverage report upload doesn't depend on specific pytest features,
        # it should always return True
        assert _pytest_version_supports_coverage_report_upload() is True


class TestCoverageReportUploadCapabilityIntegration:
    """Integration tests for coverage report upload capability in pytest plugin."""

    def test_coverage_report_upload_integration(self) -> None:
        """Test integration of coverage report upload capability with pytest plugin."""
        # Should always return True (supports all pytest versions)
        assert _pytest_version_supports_coverage_report_upload() is True

        # Check capability is properly registered and creates correct tags
        expected_tag_key = "_dd.library_capabilities.coverage_report_upload"
        assert "coverage_report_upload" in LibraryCapabilities.TAGS
        assert LibraryCapabilities.TAGS["coverage_report_upload"] == expected_tag_key

        capabilities = LibraryCapabilities(coverage_report_upload="1")
        tags = capabilities.tags()
        assert expected_tag_key in tags
        assert tags[expected_tag_key] == "1"
