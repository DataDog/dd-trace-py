"""
Coverage report generation for Test Optimization.

This module handles generating coverage reports in various formats (lcov, cobertura, etc.)
from coverage data collected during test execution.
"""

from __future__ import annotations

import gzip
import io
import logging
import os
from pathlib import Path
import tempfile
import typing as t

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.git import GitTag


log = logging.getLogger(__name__)


class CoverageReportFormat:
    """Supported coverage report formats."""

    LCOV = "lcov"
    COBERTURA = "cobertura"
    JACOCO = "jacoco"
    CLOVER = "clover"
    OPENCOVER = "opencover"
    SIMPLECOV = "simplecov"


def generate_coverage_report_lcov_from_coverage_py(cov_instance) -> t.Optional[bytes]:
    """
    Generate an LCOV format coverage report from a coverage.py instance.

    Args:
        cov_instance: The coverage.Coverage instance that was collecting data

    Returns:
        The LCOV report as bytes, or None if coverage data is not available.
    """
    if cov_instance is None:
        log.debug("No coverage instance provided")
        return None

    try:
        # Stop coverage collection
        cov_instance.stop()

        # Check if there's any data
        if not cov_instance.get_data().measured_files():
            log.debug("No coverage data available to generate report")
            return None

        # Generate LCOV report to a temporary file
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".lcov", delete=False) as temp_file:
            temp_path = temp_file.name

        try:
            cov_instance.lcov_report(outfile=temp_path)

            with open(temp_path, "rb") as f:
                lcov_data = f.read()

            log.debug("Generated LCOV coverage report from coverage.py: %d bytes", len(lcov_data))
            return lcov_data

        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    except Exception:
        log.exception("Error generating LCOV coverage report from coverage.py")
        return None


def generate_coverage_report_lcov_from_module_collector(workspace_path: Path) -> t.Optional[bytes]:
    """
    Generate an LCOV format coverage report from ModuleCodeCollector data.

    This is used as a fallback when coverage.py is not being used (no --cov flag).

    Args:
        workspace_path: The workspace path to use for relative path calculation.

    Returns:
        The LCOV report as bytes, or None if no coverage data is available.
    """
    try:
        if not ModuleCodeCollector.is_installed():
            log.debug("ModuleCodeCollector is not installed.")
            return None

        instance = ModuleCodeCollector._instance
        if instance is None:
            log.debug("ModuleCodeCollector instance is None.")
            return None

        # Get all executable lines and covered lines from the global coverage
        # (not context-based, as we want the full session coverage)
        executable_lines = instance.lines
        covered_lines = instance.covered

        if not covered_lines:
            log.debug("No coverage data collected by ModuleCodeCollector.")
            return None

        # Generate LCOV format
        lcov_lines = []
        lcov_lines.append("TN:")  # Test name (empty for aggregated report)

        for abs_path_str in sorted(covered_lines.keys()):
            abs_path = Path(abs_path_str)

            # Try to make path relative to workspace
            try:
                if abs_path.is_relative_to(workspace_path):
                    source_file = str(abs_path.relative_to(workspace_path))
                else:
                    source_file = abs_path_str
            except (ValueError, AttributeError):
                source_file = abs_path_str

            lcov_lines.append(f"SF:{source_file}")

            # Get coverage data for this file
            covered_file_lines = covered_lines[abs_path_str]
            executable_file_lines = executable_lines.get(abs_path_str, CoverageLines())

            # Convert CoverageLines to sorted list of line numbers
            covered_line_numbers = set()
            for line_num in covered_file_lines.to_sorted_list():
                if line_num > 0:  # Filter out any invalid line numbers
                    covered_line_numbers.add(line_num)

            # Get all executable line numbers
            all_line_numbers = set()
            for line_num in executable_file_lines.to_sorted_list():
                if line_num > 0:
                    all_line_numbers.add(line_num)

            # Write DA: (data) lines - format is DA:<line>,<execution_count>
            # We write all executable lines, with count 1 if covered, 0 if not
            for line_num in sorted(all_line_numbers):
                execution_count = 1 if line_num in covered_line_numbers else 0
                lcov_lines.append(f"DA:{line_num},{execution_count}")

            # Summary statistics
            lines_found = len(all_line_numbers)
            lines_hit = len(covered_line_numbers & all_line_numbers)

            lcov_lines.append(f"LF:{lines_found}")  # Lines found (total executable)
            lcov_lines.append(f"LH:{lines_hit}")  # Lines hit (covered)
            lcov_lines.append("end_of_record")

        if len(lcov_lines) <= 1:  # Only has "TN:" line
            log.debug("Generated LCOV report has no file data.")
            return None

        lcov_content = "\n".join(lcov_lines) + "\n"
        log.debug("Generated LCOV coverage report from ModuleCodeCollector: %d bytes", len(lcov_content))
        return lcov_content.encode("utf-8")

    except Exception:
        log.exception("Failed to generate LCOV coverage report from ModuleCodeCollector")
        return None


def compress_coverage_report(report_data: bytes) -> bytes:
    """
    Compress coverage report data using gzip.

    Args:
        report_data: The raw coverage report data

    Returns:
        Gzip-compressed report data
    """
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(report_data)
    return buf.getvalue()


def create_coverage_report_event(env_tags: t.Dict[str, str], report_format: str) -> bytes:
    """
    Create the event JSON for the coverage report upload.

    Args:
        env_tags: Environment tags containing git and CI information
        report_format: The format of the coverage report (e.g., "lcov")

    Returns:
        The event JSON as bytes
    """
    import json

    event = {
        "type": "coverage_report",
        "format": report_format,
    }

    # Add git tags
    for git_tag in [
        GitTag.REPOSITORY_URL,
        GitTag.COMMIT_SHA,
        GitTag.BRANCH,
        GitTag.TAG,
        GitTag.COMMIT_MESSAGE,
        GitTag.COMMIT_AUTHOR_NAME,
        GitTag.COMMIT_AUTHOR_EMAIL,
        GitTag.COMMIT_AUTHOR_DATE,
        GitTag.COMMIT_COMMITTER_NAME,
        GitTag.COMMIT_COMMITTER_EMAIL,
        GitTag.COMMIT_COMMITTER_DATE,
    ]:
        if git_tag in env_tags:
            event[git_tag] = env_tags[git_tag]

    # Add CI tags
    for ci_tag in [
        CITag.PROVIDER_NAME,
        CITag.PIPELINE_ID,
        CITag.PIPELINE_NAME,
        CITag.PIPELINE_NUMBER,
        CITag.PIPELINE_URL,
        CITag.JOB_NAME,
        CITag.JOB_URL,
        CITag.STAGE_NAME,
        CITag.WORKSPACE_PATH,
        CITag.NODE_NAME,
        CITag.NODE_LABELS,
    ]:
        if ci_tag in env_tags:
            event[ci_tag] = env_tags[ci_tag]

    # Add PR number if available
    if "git.pull_request.number" in env_tags:
        event["pr.number"] = env_tags["git.pull_request.number"]

    return json.dumps(event).encode("utf-8")
