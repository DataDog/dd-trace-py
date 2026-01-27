"""
Constants for coverage telemetry metric names.

This module provides centralized constants for all coverage-related telemetry metrics
to avoid magic strings and ensure consistency across different telemetry implementations.
"""

# Coverage upload metrics
COVERAGE_UPLOAD_REQUEST = "coverage_upload.request"
COVERAGE_UPLOAD_REQUEST_BYTES = "coverage_upload.request_bytes"
COVERAGE_UPLOAD_REQUEST_MS = "coverage_upload.request_ms"
COVERAGE_UPLOAD_REQUEST_ERRORS = "coverage_upload.request_errors"

# Code coverage metrics
CODE_COVERAGE_FILES = "code_coverage.files"
