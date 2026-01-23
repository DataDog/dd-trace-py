"""
Tests for library capabilities related to coverage report upload.
"""

from unittest.mock import Mock

import pytest

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

    @pytest.fixture
    def mock_session(self):
        """Mock pytest session."""
        session = Mock()
        session.config = Mock()
        session.config.pluginmanager = Mock()
        session.config.getoption = Mock()
        return session

    def test_coverage_report_upload_capability_is_set(self, mock_session) -> None:
        """Test that coverage report upload capability is set when pytest plugin initializes."""
        from ddtrace.contrib.internal.pytest._plugin_v2 import LibraryCapabilities
        from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_coverage_report_upload

        # Create capabilities like the plugin does
        library_capabilities = LibraryCapabilities(
            coverage_report_upload="1" if _pytest_version_supports_coverage_report_upload() else None,
        )

        tags = library_capabilities.tags()

        # Should always be set since _pytest_version_supports_coverage_report_upload() always returns True
        assert "_dd.library_capabilities.coverage_report_upload" in tags
        assert tags["_dd.library_capabilities.coverage_report_upload"] == "1"

    def test_coverage_report_upload_tag_format(self) -> None:
        """Test that the coverage report upload capability tag has correct format."""
        expected_tag_key = "_dd.library_capabilities.coverage_report_upload"

        # Check that the tag key is registered in LibraryCapabilities.TAGS
        assert "coverage_report_upload" in LibraryCapabilities.TAGS
        assert LibraryCapabilities.TAGS["coverage_report_upload"] == expected_tag_key

        # Create capability with version "1"
        capabilities = LibraryCapabilities(coverage_report_upload="1")
        tags = capabilities.tags()

        assert expected_tag_key in tags
        assert tags[expected_tag_key] == "1"
