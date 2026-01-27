"""
Consolidated coverage integration for pytest plugins.

This module provides a simplified interface for coverage collection, report generation,
and upload functionality that can be shared between V2 and V3 pytest plugins.
"""

from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import time
import typing as t

from ddtrace.contrib.internal.coverage.constants import PCT_COVERED_KEY
from ddtrace.contrib.internal.coverage.data import _coverage_data
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.testing.internal.coverage_telemetry_constants import CODE_COVERAGE_FILES
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST_BYTES
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST_ERRORS
from ddtrace.testing.internal.coverage_telemetry_constants import COVERAGE_UPLOAD_REQUEST_MS


log = get_logger(__name__)

# Supported coverage report formats
COVERAGE_FORMATS = {
    "lcov": "application/text",
    "cobertura": "application/xml",
    "jacoco": "application/xml",
    "clover": "application/xml",
    "opencover": "application/xml",
    "simplecov": "application/json",
}


def is_coverage_upload_enabled() -> bool:
    """Check if coverage upload is enabled via environment variable."""
    env_var_value = os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED", "true")
    enabled = asbool(env_var_value)
    log.debug("Coverage upload enabled: %s (from env var: %s)", enabled, env_var_value)
    return enabled


def capture_coverage_instance_from_pytest_cov(session) -> t.Optional[t.Any]:
    """Capture coverage.py instance from pytest-cov plugin."""
    try:
        # Handle both V2 (config object) and V3 (session object) plugin interfaces
        if hasattr(session, "config") and hasattr(session.config, "pluginmanager"):
            # V3 plugin: session.config.pluginmanager
            pluginmanager = session.config.pluginmanager
            log.debug("Using V3 plugin interface (session.config.pluginmanager)")
        elif hasattr(session, "pluginmanager"):
            # V2 plugin: session.pluginmanager (session is actually config)
            pluginmanager = session.pluginmanager
            log.debug("Using V2 plugin interface (session.pluginmanager)")
        else:
            log.debug("Could not find pluginmanager in session object of type %s", type(session))
            return None

        pytest_cov_plugin = pluginmanager.get_plugin("pytest_cov")
        if not pytest_cov_plugin:
            log.debug("pytest-cov plugin not found in pluginmanager")
            return None

        log.debug("Found pytest-cov plugin: %s", type(pytest_cov_plugin))

        # Try different ways to access coverage instance
        if hasattr(pytest_cov_plugin, "cov_controller") and pytest_cov_plugin.cov_controller:
            cov_instance = pytest_cov_plugin.cov_controller.cov
            if cov_instance:
                log.debug("Found coverage instance via cov_controller.cov: %s", type(cov_instance))
                return cov_instance
            else:
                log.debug("cov_controller.cov is None")

        if hasattr(pytest_cov_plugin, "cov"):
            cov_instance = pytest_cov_plugin.cov
            if cov_instance:
                log.debug("Found coverage instance via cov: %s", type(cov_instance))
                return cov_instance
            else:
                log.debug("pytest_cov_plugin.cov is None")

        if hasattr(pytest_cov_plugin, "_cov"):
            cov_instance = pytest_cov_plugin._cov
            if cov_instance:
                log.debug("Found coverage instance via _cov: %s", type(cov_instance))
                return cov_instance
            else:
                log.debug("pytest_cov_plugin._cov is None")

        # Log available attributes for debugging
        available_attrs = [attr for attr in dir(pytest_cov_plugin) if not attr.startswith('__')]
        log.debug("Available attributes in pytest-cov plugin: %s", available_attrs)
        log.debug("Could not find coverage instance in pytest-cov plugin")
        return None

    except Exception:
        log.debug("Failed to capture coverage instance", exc_info=True)
        return None


def create_coverage_instance_for_upload(include_patterns=None) -> t.Optional[t.Any]:
    """Create a new coverage instance specifically for coverage upload."""
    try:
        import coverage

        # Create coverage instance with default configuration
        cov = coverage.Coverage(
            data_suffix=None,
            config_file=True,  # Look for .coveragerc
            include=include_patterns,
            branch=False,  # Line coverage for now
            auto_data=True,
            timid=False,
        )

        log.debug("Created coverage instance for upload")
        return cov

    except ImportError:
        log.debug("Coverage.py not available, cannot create coverage instance")
        return None
    except Exception:
        log.exception("Failed to create coverage instance")
        return None


class CoverageInstanceProvider:
    """Handles creation and management of coverage instances."""

    def __init__(self):
        self._coverage_instance = None

    def get_or_create_coverage_instance(self, config) -> t.Optional[t.Any]:
        """Get existing or create new coverage instance based on configuration."""
        if self._coverage_instance:
            return self._coverage_instance

        # Try to get existing coverage instance from pytest-cov first
        coverage_instance = capture_coverage_instance_from_pytest_cov(config)
        if coverage_instance and hasattr(coverage_instance, 'stop'):
            self._coverage_instance = coverage_instance
            return coverage_instance

        # If upload is enabled but no valid coverage instance exists, create one
        if is_coverage_upload_enabled():
            coverage_instance = self._create_coverage_instance_if_needed(config)
            if coverage_instance:
                self._coverage_instance = coverage_instance
                return coverage_instance

        return None

    def initialize_coverage_early(self, config) -> t.Optional[t.Any]:
        """Initialize coverage early if upload is enabled but no pytest-cov detected."""
        if not is_coverage_upload_enabled():
            return None

        try:
            pytest_cov_enabled = self._is_pytest_cov_enabled_for_config(config)
            if not pytest_cov_enabled:
                log.debug("Coverage upload enabled but no --cov detected, starting coverage collection")
                coverage_instance = self._create_and_start_coverage_instance(config)
                if coverage_instance:
                    self._coverage_instance = coverage_instance
                    return coverage_instance
        except Exception:
            # Avoid any issues during early initialization that might interfere with test execution
            log.debug("Failed to initialize coverage early, will retry during session finish", exc_info=True)

        return None

    def _create_coverage_instance_if_needed(self, config) -> t.Optional[t.Any]:
        """Create coverage instance if needed for upload when no pytest-cov coverage exists."""
        log.debug("No existing coverage instance found, creating one for upload")

        # Determine include patterns from current working directory
        cwd = Path.cwd()
        include_patterns = [
            str(cwd / "*.py"),
            str(cwd / "*" / "*.py"),
            str(cwd / "*" / "*" / "*.py"),  # Basic depth coverage
        ]

        coverage_instance = create_coverage_instance_for_upload(include_patterns)
        if coverage_instance:
            # Start coverage collection retroactively if possible
            try:
                coverage_instance.start()
                log.debug("Started coverage collection for upload")

                # Try to collect any existing coverage data if available
                if hasattr(coverage_instance, "collect"):
                    coverage_instance.collect()

            except Exception:
                log.debug("Could not start retroactive coverage collection", exc_info=True)

        return coverage_instance

    def _create_and_start_coverage_instance(self, config) -> t.Optional[t.Any]:
        """Create and start a coverage instance for upload when no pytest-cov exists."""
        log.debug("Creating and starting coverage collection for upload")

        try:
            # Determine include patterns based on test paths or current directory
            include_patterns = self._get_coverage_include_patterns(config)

            coverage_instance = create_coverage_instance_for_upload(include_patterns)
            if coverage_instance:
                try:
                    coverage_instance.start()
                    log.debug("Started coverage collection for upload with patterns: %s", include_patterns)
                    return coverage_instance
                except Exception:
                    log.debug("Failed to start coverage collection during early init", exc_info=True)
                    return None
        except Exception:
            # Be extra defensive during early initialization to avoid interfering with test setup
            log.debug("Failed to create coverage instance during early init", exc_info=True)

        return None

    def _get_coverage_include_patterns(self, config) -> t.List[str]:
        """Get coverage include patterns based on test configuration."""
        try:
            cwd = Path.cwd()
            patterns = []

            try:
                # Look for common source directories, avoid any operations that might trigger git commands
                common_dirs = ["src", "lib", "app", "ddtrace"]  # Use common names instead of cwd.name
                for dir_name in common_dirs:
                    dir_path = cwd / dir_name
                    if dir_path.exists() and dir_path.is_dir():
                        patterns.append(str(dir_path / "*.py"))
                        patterns.append(str(dir_path / "*" / "*.py"))

                # If no common directories found, use current directory
                if not patterns:
                    patterns = [
                        str(cwd / "*.py"),
                        str(cwd / "*" / "*.py"),
                    ]

            except Exception:
                # Fallback to current directory
                patterns = [
                    str(cwd / "*.py"),
                    str(cwd / "*" / "*.py"),
                ]

            return patterns

        except Exception:
            # Ultimate fallback - minimal pattern to avoid any issues
            log.debug("Failed to determine coverage patterns, using minimal fallback", exc_info=True)
            return ["*.py", "*/*.py"]

    def _is_pytest_cov_enabled_for_config(self, config) -> bool:
        """Check if pytest-cov is enabled for the given config."""
        try:
            if not hasattr(config, "pluginmanager"):
                return False

            pytest_cov_plugin = config.pluginmanager.get_plugin("pytest_cov")
            if not pytest_cov_plugin:
                return False

            # Check if --cov option was provided
            cov_option = config.getoption("--cov", default=None)
            nocov_option = config.getoption("--no-cov", default=False)

            if nocov_option:
                return False

            return bool(cov_option)
        except Exception:
            log.debug("Could not determine pytest-cov status", exc_info=True)
            return False


class CoveragePercentageHandler:
    """Handles the legacy coverage percentage logic."""

    def __init__(self, telemetry_recorder):
        self.telemetry_recorder = telemetry_recorder

    def handle_coverage_percentage(self, config, session_span=None, session_manager=None) -> None:
        """Extract and set coverage percentage using existing logic."""
        # Handle existing percentage logic
        invoked_by_coverage_run = _is_coverage_invoked_by_coverage_run()
        pytest_cov_enabled = self._is_pytest_cov_enabled(config)
        coverage_patched = _is_coverage_patched()

        log.debug(
            "Coverage percentage handler - patched: %s, pytest-cov: %s, coverage run: %s",
            coverage_patched,
            pytest_cov_enabled,
            invoked_by_coverage_run,
        )

        if coverage_patched and (pytest_cov_enabled or invoked_by_coverage_run):
            if invoked_by_coverage_run and not pytest_cov_enabled:
                run_coverage_report()

            lines_pct_value = _coverage_data.get(PCT_COVERED_KEY, None)

            if lines_pct_value is None:
                log.debug("No coverage data available")
                self.telemetry_recorder.record_telemetry("coverage.empty", 1)
            elif isinstance(lines_pct_value, (float, int)):
                log.debug("Setting coverage percentage: %.2f%%", lines_pct_value)
                if session_span:
                    session_span.set_tag("test.code_coverage.lines_pct", lines_pct_value)
                elif session_manager and hasattr(session_manager, "set_covered_lines_pct"):
                    session_manager.set_covered_lines_pct(lines_pct_value)
            else:
                log.warning("Unexpected coverage percentage format: %r", lines_pct_value)

    def _is_pytest_cov_enabled(self, config) -> bool:
        """Check if pytest-cov is enabled."""
        if not config.pluginmanager.get_plugin("pytest_cov"):
            return False
        cov_option = config.getoption("--cov", default=False)
        nocov_option = config.getoption("--no-cov", default=False)
        if nocov_option:
            return False
        return bool(cov_option)


class LcovReportGenerator:
    """Generates LCOV format coverage reports."""

    def __init__(self, telemetry_recorder):
        self.telemetry_recorder = telemetry_recorder

    def generate_from_coverage_instance(self, coverage_instance) -> t.Optional[bytes]:
        """Generate LCOV report from coverage.py instance."""
        try:
            if not coverage_instance:
                return None

            # Stop coverage collection
            coverage_instance.stop()
            cov_data = coverage_instance.get_data()
            measured_files = cov_data.measured_files()

            if not measured_files:
                log.debug("No measured files found in coverage data")
                return None

            lcov_lines = ["TN:"]  # Test name (empty)

            for abs_path_str in sorted(measured_files):
                executed_lines = set(cov_data.lines(abs_path_str) or [])
                if not executed_lines:
                    continue

                # Use relative path if possible
                file_path = self._get_relative_path(abs_path_str)
                lcov_lines.append(f"SF:{file_path}")

                # Add line data
                valid_lines = [ln for ln in executed_lines if ln > 0]
                for line_num in sorted(valid_lines):
                    lcov_lines.append(f"DA:{line_num},1")

                # Summary
                lines_hit = len(valid_lines)
                lcov_lines.extend([f"LF:{lines_hit}", f"LH:{lines_hit}", "end_of_record"])

            if len(lcov_lines) <= 1:
                log.debug("No coverage data to report")
                return None

            content = "\n".join(lcov_lines) + "\n"

            # Record files processed telemetry
            files_with_coverage = len([line for line in lcov_lines if line.startswith("SF:")])
            self.telemetry_recorder.record_telemetry(CODE_COVERAGE_FILES, files_with_coverage)

            log.debug("Generated LCOV report: %d bytes for %d files", len(content), files_with_coverage)
            return content.encode("utf-8")

        except Exception:
            log.exception("Failed to generate LCOV report")
            return None

    def _get_relative_path(self, abs_path_str: str) -> str:
        """Convert absolute path to relative path if possible."""
        try:
            abs_path = Path(abs_path_str)
            workspace = Path.cwd()
            if abs_path.is_relative_to(workspace):
                return str(abs_path.relative_to(workspace))
            else:
                return abs_path_str
        except (ValueError, AttributeError):
            return abs_path_str


class CoverageEventBuilder:
    """Builds coverage report event metadata with git and CI tags."""

    def __init__(self, env_tags=None):
        self.env_tags = env_tags or {}

    def create_coverage_report_event(self, report_format: str) -> dict:
        """Create the event JSON for the coverage report upload with git and CI tags."""
        event = {
            "type": "coverage_report",
            "format": report_format,
            "timestamp": int(time.time() * 1000),
        }

        # Import git and CI tag constants
        try:
            from ddtrace.testing.internal.ci import CITag
            from ddtrace.testing.internal.git import GitTag
        except ImportError:
            log.debug("Could not import GitTag/CITag constants")
            return event

        # Add git tags
        git_tags = [
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
        ]

        for git_tag in git_tags:
            if git_tag in self.env_tags:
                event[git_tag] = self.env_tags[git_tag]

        # Add CI tags
        ci_tags = [
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
        ]

        for ci_tag in ci_tags:
            if ci_tag in self.env_tags:
                event[ci_tag] = self.env_tags[ci_tag]

        # Add PR number if available
        if "git.pull_request.number" in self.env_tags:
            event["pr.number"] = self.env_tags["git.pull_request.number"]

        return event


class CoverageUploadStrategy:
    """Strategy for uploading coverage reports via V2 or V3 infrastructure."""

    def __init__(self, telemetry_recorder, event_builder):
        self.telemetry_recorder = telemetry_recorder
        self.event_builder = event_builder

    def upload_via_v3_infrastructure(self, session_manager, coverage_data: bytes, report_format: str) -> bool:
        """Upload coverage report using V3 plugin's existing infrastructure."""
        try:
            # Use the existing coverage_writer from session manager
            if not hasattr(session_manager, "coverage_writer"):
                log.debug("V3 plugin: No coverage_writer available")
                return False

            coverage_writer = session_manager.coverage_writer
            connector = coverage_writer.connector

            # Upload using existing connector infrastructure
            return self._upload_via_connector(connector, coverage_data, report_format)

        except Exception:
            log.exception("V3 plugin: Failed to upload coverage report")
            return False

    def upload_via_v2_infrastructure(self, coverage_data: bytes, report_format: str) -> bool:
        """Upload coverage report using V2 plugin's existing infrastructure."""
        try:
            # For V2 plugin, we need to use the CIVisibilityWriter's HTTP infrastructure
            from ddtrace.internal.ci_visibility.service_registry import require_ci_visibility_service

            ci_service = require_ci_visibility_service()

            # Get the writer from the CI service tracer
            writer = getattr(ci_service.tracer._span_aggregator, "writer", None)
            if not writer:
                log.debug("V2 plugin: No writer available in CI service")
                return False

            # Use V2-specific upload method that works with CIVisibilityWriter
            return self._upload_coverage_via_v2_writer(writer, coverage_data, report_format)

        except Exception:
            log.exception("V2 plugin: Failed to upload coverage report")
            return False

    def _upload_coverage_via_v2_writer(self, writer, lcov_data: bytes, report_format: str) -> bool:
        """Upload coverage report using V2 plugin's CIVisibilityWriter."""
        try:
            # Compress the LCOV data
            compressed_data = self._compress_data(lcov_data)

            # Create event JSON with git and CI tags
            event = self.event_builder.create_coverage_report_event(report_format)
            event_json = json.dumps(event, separators=(",", ":")).encode("utf-8")

            # Create multipart form data manually (since CIVisibilityWriter doesn't support post_files)
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            multipart_data = self._create_multipart_data(boundary, compressed_data, event_json, report_format)

            # Record telemetry
            total_size = len(multipart_data)
            self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST, 1, {"format": report_format})
            self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_BYTES, total_size)

            # Upload using writer's HTTP infrastructure with authentication
            log.debug("V2 plugin: Uploading %d bytes coverage report to /api/v2/cicovreprt", total_size)
            start_time = time.time()

            # Create headers with proper authentication and content type
            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(multipart_data)),
            }

            # Use the writer's built-in HTTP client infrastructure with authentication
            success = self._send_coverage_via_writer_client(writer, multipart_data, headers)

            duration_ms = (time.time() - start_time) * 1000

            if success:
                log.debug("V2 plugin: Coverage upload successful in %.2fms", duration_ms)
                self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_MS, duration_ms)
                return True
            else:
                log.error("V2 plugin: Coverage upload failed")
                self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": "http_error"})
                return False

        except Exception:
            log.exception("V2 plugin: Exception during coverage report upload")
            self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": "exception"})
            return False

    def _create_multipart_data(
        self, boundary: str, coverage_data: bytes, event_data: bytes, report_format: str
    ) -> bytes:
        """Create multipart form data for coverage upload."""
        parts = []

        # Add coverage file
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            f'Content-Disposition: form-data; name="coverage"; filename="coverage.{report_format}.gz"\r\n'.encode(
                "utf-8"
            )
        )
        parts.append(b"Content-Type: application/gzip\r\n\r\n")
        parts.append(coverage_data)
        parts.append(b"\r\n")

        # Add event file
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(b'Content-Disposition: form-data; name="event"; filename="event.json"\r\n')
        parts.append(b"Content-Type: application/json\r\n\r\n")
        parts.append(event_data)
        parts.append(b"\r\n")

        # End boundary
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))

        return b"".join(parts)

    def _send_coverage_via_writer_client(self, writer, data: bytes, headers: dict) -> bool:
        """Send coverage data via writer's HTTP client infrastructure with authentication."""
        try:
            # Use the writer's existing client infrastructure for coverage uploads
            # The CIVisibilityWriter has coverage clients that handle authentication
            coverage_clients = [
                client
                for client in writer.clients
                if hasattr(client, "ENDPOINT") and "cov" in getattr(client, "ENDPOINT", "")
            ]

            if not coverage_clients:
                log.debug("V2 plugin: No coverage client available in writer")
                return False

            coverage_client = coverage_clients[0]

            # Use the writer's _put method with the coverage client
            response = writer._put(data, headers, coverage_client, no_trace=True)

            status_code = response.status
            log.debug("V2 plugin: Coverage upload HTTP response: %d", status_code)
            return 200 <= status_code < 300

        except Exception as e:
            log.debug("V2 plugin: HTTP request via writer client failed: %s", e)
            return False

    def _upload_via_connector(self, connector, lcov_data: bytes, report_format: str) -> bool:
        """Upload coverage report using the provided connector."""
        try:
            from ddtrace.testing.internal.http import FileAttachment

            # Compress the LCOV data
            compressed_data = self._compress_data(lcov_data)

            # Create event JSON with git and CI tags
            event = self.event_builder.create_coverage_report_event(report_format)
            event_json = json.dumps(event, separators=(",", ":")).encode("utf-8")

            # Create file attachments
            files = [
                FileAttachment(
                    name="coverage",
                    filename=f"coverage.{report_format}.gz",
                    content_type="application/gzip",
                    data=compressed_data,
                ),
                FileAttachment(
                    name="event",
                    filename="event.json",
                    content_type="application/json",
                    data=event_json,
                ),
            ]

            # Record telemetry
            total_size = len(compressed_data) + len(event_json)
            self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST, 1, {"format": report_format})
            self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_BYTES, total_size)

            # Upload to coverage report intake endpoint
            log.debug("Uploading %d bytes coverage report to /api/v2/cicovreprt", total_size)
            start_time = time.time()

            result = connector.post_files(
                "/api/v2/cicovreprt",
                files=files,
                send_gzip=False,  # Already compressed
            )

            duration_ms = (time.time() - start_time) * 1000

            if hasattr(result, "error_type") and result.error_type:
                log.error("Coverage upload failed: %s", result.error_type)
                self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": result.error_type})
                return False
            else:
                log.debug("Coverage upload successful in %.2fms", duration_ms)
                self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_MS, duration_ms)
                return True

        except Exception:
            log.exception("Exception during coverage report upload")
            self.telemetry_recorder.record_telemetry(COVERAGE_UPLOAD_REQUEST_ERRORS, 1, {"error": "exception"})
            return False

    def _compress_data(self, data: bytes) -> bytes:
        """Compress data using gzip."""
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(data)
        return buf.getvalue()


class TelemetryRecorder:
    """Handles telemetry recording for both V2 and V3 plugins."""

    def __init__(self, telemetry_writer=None, session_manager=None):
        self.telemetry_writer = telemetry_writer
        self.session_manager = session_manager

    def record_telemetry(self, metric_name: str, value: t.Union[int, float], tags: t.Optional[t.Dict] = None):
        """Record telemetry metric."""
        try:
            if self.telemetry_writer:
                # V2 plugin telemetry
                from ddtrace.internal.telemetry import telemetry_writer
                from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE

                tag_pairs = tuple(tags.items()) if tags else ()
                telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, metric_name, value, tag_pairs)

            elif self.session_manager and hasattr(self.session_manager, "telemetry_api"):
                # V3 plugin telemetry
                telemetry_api = self.session_manager.telemetry_api
                if hasattr(telemetry_api, "add_count_metric"):
                    telemetry_api.add_count_metric(metric_name, value, tags or {})

        except Exception:
            log.debug("Failed to record telemetry", exc_info=True)


class CoverageIntegration:
    """Main coverage integration facade that coordinates all coverage functionality."""

    def __init__(self, telemetry_writer=None, session_manager=None, env_tags=None):
        self.session_manager = session_manager
        self.env_tags = env_tags or {}

        # Create component dependencies
        self.telemetry_recorder = TelemetryRecorder(telemetry_writer, session_manager)
        self.coverage_provider = CoverageInstanceProvider()
        self.percentage_handler = CoveragePercentageHandler(self.telemetry_recorder)
        self.report_generator = LcovReportGenerator(self.telemetry_recorder)
        self.event_builder = CoverageEventBuilder(env_tags)
        self.upload_strategy = CoverageUploadStrategy(self.telemetry_recorder, self.event_builder)

        self._initialized = False

    def initialize(self, config=None):
        """Initialize the coverage integration."""
        if self._initialized:
            return

        # Initialize early coverage collection if needed
        if config and is_coverage_upload_enabled():
            self.coverage_provider.initialize_coverage_early(config)

        self.telemetry_recorder.record_telemetry("coverage.started", 1, {"library": "coveragepy", "framework": "pytest"})
        self._initialized = True

    def handle_session_finish(self, config, session_span=None):
        """Handle pytest session finish - extract coverage percentage and upload reports."""
        if not self._initialized:
            return

        # Handle coverage percentage (existing legacy logic)
        self.percentage_handler.handle_coverage_percentage(config, session_span, self.session_manager)

        # Handle coverage report upload if enabled
        if is_coverage_upload_enabled():
            self._handle_coverage_upload(config)

        self.telemetry_recorder.record_telemetry("coverage.finished", 1, {"library": "coveragepy", "framework": "pytest"})

    def _handle_coverage_upload(self, config):
        """Handle coverage report generation and upload."""
        coverage_instance = self.coverage_provider.get_or_create_coverage_instance(config)
        if not coverage_instance or not hasattr(coverage_instance, 'stop'):
            log.debug("No valid coverage instance available for upload")
            return

        # Generate report
        lcov_data = self.report_generator.generate_from_coverage_instance(coverage_instance)
        if not lcov_data:
            log.debug("No coverage data to upload")
            return

        # Upload using appropriate strategy
        success = False
        if self.session_manager:
            # V3 plugin: Use existing coverage_writer infrastructure
            success = self.upload_strategy.upload_via_v3_infrastructure(self.session_manager, lcov_data, "lcov")
        else:
            # V2 plugin: Use existing telemetry infrastructure
            success = self.upload_strategy.upload_via_v2_infrastructure(lcov_data, "lcov")

        if success:
            log.debug("Coverage report uploaded successfully")
        else:
            log.debug("Failed to upload coverage report")