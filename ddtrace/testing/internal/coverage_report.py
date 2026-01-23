"""
Coverage report generation for Test Optimization.

This module handles generating coverage reports in various formats (lcov, cobertura, etc.)
from coverage data collected during test execution.
"""

from __future__ import annotations

import gzip
import io
import logging
from pathlib import Path
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


def generate_coverage_report_lcov_from_coverage_py(
    cov_instance,
    workspace_path: Path,
    skippable_coverage: t.Dict[str, t.Set[int]],
) -> t.Optional[bytes]:
    """
    Generate an LCOV format coverage report from a coverage.py instance.

    If skippable_coverage is provided, merges it with the local coverage data.

    Args:
        cov_instance: The coverage.Coverage instance that was collecting data
        workspace_path: The workspace path to use for relative path calculation
        skippable_coverage: Coverage data for ITR-skipped tests (file_path -> set of line numbers)

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
        measured_files = cov_instance.get_data().measured_files()
        if not measured_files and not skippable_coverage:
            log.debug("No coverage data available to generate report")
            return None

        # Always use our enhanced LCOV generation for accuracy
        # This ensures both covered and uncovered executable lines are properly reported
        log.debug("Generating enhanced LCOV report with precise line reporting")
        return _generate_merged_lcov_from_coverage_py(cov_instance, workspace_path, skippable_coverage)

    except Exception:
        log.exception("Error generating LCOV coverage report from coverage.py")
        return None


def _generate_merged_lcov_from_coverage_py(
    cov_instance,
    workspace_path: Path,
    skippable_coverage: t.Dict[str, t.Set[int]],
) -> t.Optional[bytes]:
    """
    Generate LCOV report by merging coverage.py data with ITR skipped test coverage.

    This function manually constructs the LCOV report by:
    1. Getting line-by-line coverage from coverage.py
    2. Merging it with backend coverage data (union)
    3. Formatting as LCOV

    Args:
        cov_instance: The coverage.Coverage instance
        workspace_path: The workspace path for path resolution
        skippable_coverage: Backend coverage for skipped tests

    Returns:
        LCOV report as bytes
    """
    try:
        # Get coverage data from coverage.py
        cov_data = cov_instance.get_data()

        # Build maps of relative paths to coverage data
        local_coverage: t.Dict[str, t.Set[int]] = {}
        executable_lines_map: t.Dict[str, t.Set[int]] = {}

        for abs_path_str in cov_data.measured_files():
            abs_path = Path(abs_path_str)

            # Make path relative to workspace
            try:
                if abs_path.is_relative_to(workspace_path):
                    relative_path = str(abs_path.relative_to(workspace_path))
                else:
                    relative_path = abs_path_str
            except (ValueError, AttributeError):
                relative_path = abs_path_str

            # Get executed lines for this file
            executed_lines = set(cov_data.lines(abs_path_str) or [])
            local_coverage[relative_path] = executed_lines

            # Get missing lines (executable but not covered)
            missing_lines = set(cov_data.missing(abs_path_str) or [])
            # Total executable lines = covered + missing
            executable_lines = executed_lines | missing_lines
            executable_lines_map[relative_path] = executable_lines

        # Merge with skippable coverage (union)
        merged_coverage: t.Dict[str, t.Set[int]] = {}
        all_executable: t.Dict[str, t.Set[int]] = {}

        # Add local coverage and executable lines
        for file_path, lines in local_coverage.items():
            merged_coverage[file_path] = lines.copy()
            all_executable[file_path] = executable_lines_map[file_path].copy()

        # Merge with backend coverage
        for file_path, backend_lines in skippable_coverage.items():
            if file_path in merged_coverage:
                merged_coverage[file_path] |= backend_lines
                # Add backend lines to executable lines (they must be executable to be covered)
                all_executable[file_path] |= backend_lines
            else:
                merged_coverage[file_path] = backend_lines.copy()
                # For backend-only files, assume all covered lines are executable
                all_executable[file_path] = backend_lines.copy()

        log.debug(
            "Merged coverage: %d local files, %d backend files, %d total files",
            len(local_coverage),
            len(skippable_coverage),
            len(merged_coverage),
        )

        # Generate LCOV format
        lcov_lines = ["TN:"]  # Test name (empty for aggregated report)

        for file_path in sorted(merged_coverage.keys()):
            covered_lines = merged_coverage[file_path]
            executable_lines = all_executable.get(file_path, set())

            # Skip files with no executable lines
            if not executable_lines:
                continue

            lcov_lines.append(f"SF:{file_path}")

            # Write DA: (data) lines - format is DA:<line>,<execution_count>
            # We write all executable lines, with count 1 if covered, 0 if not
            for line_num in sorted(executable_lines):
                if line_num > 0:  # Skip line 0 (invalid source line)
                    execution_count = 1 if line_num in covered_lines else 0
                    lcov_lines.append(f"DA:{line_num},{execution_count}")

            # Summary statistics
            lines_found = len([ln for ln in executable_lines if ln > 0])
            lines_hit = len([ln for ln in covered_lines if ln > 0 and ln in executable_lines])

            lcov_lines.append(f"LF:{lines_found}")  # Lines found (total executable)
            lcov_lines.append(f"LH:{lines_hit}")  # Lines hit (covered)
            lcov_lines.append("end_of_record")

        if len(lcov_lines) <= 1:
            log.debug("Generated merged LCOV report has no file data.")
            return None

        lcov_content = "\n".join(lcov_lines) + "\n"
        log.debug("Generated merged LCOV coverage report: %d bytes", len(lcov_content))
        return lcov_content.encode("utf-8")

    except Exception:
        log.exception("Error generating merged LCOV coverage report")
        return None


def generate_coverage_report_lcov_from_module_collector(
    workspace_path: Path,
    skippable_coverage: t.Dict[str, t.Set[int]],
) -> t.Optional[bytes]:
    """
    Generate an LCOV format coverage report from ModuleCodeCollector data.

    This is used as a fallback when coverage.py is not being used (no --cov flag).
    If skippable_coverage is provided, merges it with the local coverage data.

    Args:
        workspace_path: The workspace path to use for relative path calculation.
        skippable_coverage: Coverage data for ITR-skipped tests (file_path -> set of line numbers)

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

        if not covered_lines and not skippable_coverage:
            log.debug("No coverage data collected by ModuleCodeCollector.")
            return None

        # Build a map of relative paths to local coverage
        local_coverage_by_relative_path: t.Dict[str, t.Set[int]] = {}
        executable_by_relative_path: t.Dict[str, t.Set[int]] = {}

        for abs_path_str in covered_lines.keys():
            abs_path = Path(abs_path_str)

            # Try to make path relative to workspace
            try:
                if abs_path.is_relative_to(workspace_path):
                    relative_path = str(abs_path.relative_to(workspace_path))
                else:
                    relative_path = abs_path_str
            except (ValueError, AttributeError):
                relative_path = abs_path_str

            # Get coverage data for this file
            covered_file_lines = covered_lines[abs_path_str]
            executable_file_lines = executable_lines.get(abs_path_str, CoverageLines())

            # Convert CoverageLines to sets
            covered_line_numbers = set()
            for line_num in covered_file_lines.to_sorted_list():
                if line_num > 0:
                    covered_line_numbers.add(line_num)

            all_line_numbers = set()
            for line_num in executable_file_lines.to_sorted_list():
                if line_num > 0:
                    all_line_numbers.add(line_num)

            local_coverage_by_relative_path[relative_path] = covered_line_numbers
            executable_by_relative_path[relative_path] = all_line_numbers

        # Merge with skippable coverage (union)
        merged_coverage: t.Dict[str, t.Set[int]] = {}
        all_executable: t.Dict[str, t.Set[int]] = {}

        # Start with local coverage
        for file_path, lines in local_coverage_by_relative_path.items():
            merged_coverage[file_path] = lines.copy()
            all_executable[file_path] = executable_by_relative_path[file_path].copy()

        # Merge with backend coverage
        for file_path, backend_lines in skippable_coverage.items():
            if file_path in merged_coverage:
                merged_coverage[file_path] |= backend_lines
            else:
                merged_coverage[file_path] = backend_lines.copy()
                # For backend-only files, we don't have executable line info,
                # so we assume all covered lines are executable
                all_executable[file_path] = backend_lines.copy()

        if skippable_coverage:
            log.debug(
                "Merged ModuleCodeCollector coverage: %d local files, %d backend files, %d total files",
                len(local_coverage_by_relative_path),
                len(skippable_coverage),
                len(merged_coverage),
            )

        # Generate LCOV format
        lcov_lines = ["TN:"]  # Test name (empty for aggregated report)

        for file_path in sorted(merged_coverage.keys()):
            covered_line_numbers = merged_coverage[file_path]
            executable_line_numbers = all_executable.get(file_path, covered_line_numbers)

            if not covered_line_numbers and not executable_line_numbers:
                continue

            lcov_lines.append(f"SF:{file_path}")

            # Write DA: (data) lines - format is DA:<line>,<execution_count>
            # We write all executable lines, with count 1 if covered, 0 if not
            for line_num in sorted(executable_line_numbers):
                execution_count = 1 if line_num in covered_line_numbers else 0
                lcov_lines.append(f"DA:{line_num},{execution_count}")

            # Summary statistics
            lines_found = len(executable_line_numbers)
            lines_hit = len(covered_line_numbers & executable_line_numbers)

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
