from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
import typing as t
import uuid

from ddtrace.testing.internal.constants import EMPTY_NAME
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.http import Subdomain
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import ITRSkippingLevel
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


log = logging.getLogger(__name__)

_KNOWN_TESTS_PAGE_SIZE = 2000
_KNOWN_TESTS_MAX_PAGES = 1000


class APIClient:
    def __init__(
        self,
        service: str,
        env: str,
        env_tags: dict[str, str],
        itr_skipping_level: ITRSkippingLevel,
        configurations: dict[str, str],
        connector_setup: BackendConnectorSetup,
        telemetry_api: TelemetryAPI,
    ) -> None:
        self.service = service
        self.env = env
        self.env_tags = env_tags
        self.itr_skipping_level = itr_skipping_level
        self.configurations = configurations
        self.connector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        self.coverage_connector = connector_setup.get_connector_for_subdomain(Subdomain.CICOVREPRT)
        self.telemetry_api = telemetry_api

    def close(self) -> None:
        self.connector.close()
        self.coverage_connector.close()

    def get_settings(self) -> Settings:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="git_requests.settings",
            duration="git_requests.settings_ms",
            response_bytes=None,
            error="git_requests.settings_errors",
        )

        try:
            request_data = {
                "data": {
                    "id": str(uuid.uuid4()),
                    "type": "ci_app_test_service_libraries_settings",
                    "attributes": {
                        "test_level": self.itr_skipping_level.value,
                        "service": self.service,
                        "env": self.env,
                        "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                        "sha": self.env_tags[GitTag.COMMIT_SHA],
                        "branch": self.env_tags[GitTag.BRANCH],
                        "configurations": self.configurations,
                    },
                }
            }

        except KeyError as e:
            log.warning("Git info not available, cannot fetch settings (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return Settings()

        try:
            result = self.connector.post_json(
                "/api/v2/libraries/tests/services/setting", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.warning("Error getting settings from API: %s", e)
            return Settings()

        try:
            attributes = result.parsed_response["data"]["attributes"]
            settings = Settings.from_attributes(attributes)

        except Exception as e:
            log.warning("Error getting settings from API: %s", e)
            telemetry.record_error(ErrorType.BAD_JSON)
            return Settings()

        self.telemetry_api.record_settings(settings)
        return settings

    def get_known_tests(self) -> set[TestRef]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="known_tests.request",
            duration="known_tests.request_ms",
            response_bytes="known_tests.response_bytes",
            error="known_tests.request_errors",
        )

        page_state: t.Optional[str] = None
        known_test_ids: set[TestRef] = set()

        for page_number in range(_KNOWN_TESTS_MAX_PAGES):
            page_info: dict[str, t.Any] = {"page_size": _KNOWN_TESTS_PAGE_SIZE}
            if page_state:
                page_info["page_state"] = page_state

            try:
                request_data: dict[str, t.Any] = {
                    "data": {
                        "id": str(uuid.uuid4()),
                        "type": "ci_app_libraries_tests_request",
                        "attributes": {
                            "service": self.service,
                            "env": self.env,
                            "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                            "configurations": self.configurations,
                            "page_info": page_info,
                        },
                    }
                }

            except KeyError as e:
                log.warning("Git info not available, cannot fetch known tests (missing key: %s)", e)
                telemetry.record_error(ErrorType.UNKNOWN)
                return set()

            try:
                result = self.connector.post_json("/api/v2/ci/libraries/tests", request_data, telemetry=telemetry)
                result.on_error_raise_exception()

            except Exception as e:
                log.warning("Error getting known tests from API: %s", e)
                return set()

            try:
                attributes = result.parsed_response["data"]["attributes"]
                tests_data = attributes["tests"]

                for module, suites in tests_data.items():
                    module_ref = ModuleRef(module)
                    for suite, tests in suites.items():
                        suite_ref = SuiteRef(module_ref, suite)
                        for test in tests:
                            known_test_ids.add(TestRef(suite_ref, test))

                page_info = attributes.get("page_info")
                if not page_info:
                    break

                has_next = page_info.get("has_next")
                if not has_next:
                    break

                page_state = page_info.get("cursor")
                if not page_state:
                    log.warning("Known tests response missing pagination cursor on page %d", page_number + 1)
                    telemetry.record_error(ErrorType.BAD_JSON)
                    return set()

            except Exception:
                log.warning("Error getting known tests from API")
                telemetry.record_error(ErrorType.BAD_JSON)
                return set()
        else:
            log.warning("Known tests pagination exceeded max pages: %d", _KNOWN_TESTS_MAX_PAGES)
            telemetry.record_error(ErrorType.BAD_JSON)
            return set()

        self.telemetry_api.record_known_tests_count(len(known_test_ids))
        return known_test_ids

    def get_test_management_properties(self) -> dict[TestRef, TestProperties]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="test_management_tests.request",
            duration="test_management_tests.request_ms",
            response_bytes="test_management_tests.response_bytes",
            error="test_management_tests.request_errors",
        )

        try:
            commit_message = self.env_tags.get(GitTag.COMMIT_HEAD_MESSAGE) or self.env_tags[GitTag.COMMIT_MESSAGE]
            commit_sha = self.env_tags.get(GitTag.COMMIT_HEAD_SHA) or self.env_tags[GitTag.COMMIT_SHA]

            request_data = {
                "data": {
                    "id": str(uuid.uuid4()),
                    "type": "ci_app_libraries_tests_request",
                    "attributes": {
                        "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                        "commit_message": commit_message,
                        "sha": commit_sha,
                    },
                }
            }

        except KeyError as e:
            log.warning("Git info not available, cannot fetch Test Management properties (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return {}

        try:
            result = self.connector.post_json(
                "/api/v2/test/libraries/test-management/tests", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.warning("Error getting Test Management properties from API: %s", e)
            return {}

        try:
            test_properties: dict[TestRef, TestProperties] = {}
            modules = result.parsed_response["data"]["attributes"]["modules"]

            for module_name, module_data in modules.items():
                module_ref = ModuleRef(module_name)
                suites = module_data["suites"]
                for suite_name, suite_data in suites.items():
                    suite_ref = SuiteRef(module_ref, suite_name)
                    tests = suite_data["tests"]
                    for test_name, test_data in tests.items():
                        test_ref = TestRef(suite_ref, test_name)
                        properties = test_data.get("properties", {})
                        test_properties[test_ref] = TestProperties(
                            quarantined=properties.get("quarantined", False),
                            disabled=properties.get("disabled", False),
                            attempt_to_fix=properties.get("attempt_to_fix", False),
                        )

        except Exception as e:
            log.warning("Failed to parse Test Management tests data from API: %s", e)
            telemetry.record_error(ErrorType.BAD_JSON)
            return {}

        self.telemetry_api.record_test_management_tests_count(len(test_properties))
        return test_properties

    def get_known_commits(self, latest_commits: list[str]) -> list[str]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="git_requests.search_commits",
            duration="git_requests.search_commits_ms",
            response_bytes=None,
            error="git_requests.search_commits_errors",
        )

        try:
            request_data = {
                "meta": {
                    "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                },
                "data": [{"id": sha, "type": "commit"} for sha in latest_commits],
            }

        except KeyError as e:
            log.warning("Git info not available, cannot fetch known commits (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return []

        try:
            result = self.connector.post_json(
                "/api/v2/git/repository/search_commits", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.warning("Error getting known commits from API: %s", e)
            return []

        try:
            known_commits = [item["id"] for item in result.parsed_response["data"] if item["type"] == "commit"]

        except Exception as e:
            log.warning("Failed to parse search_commits data: %s", e)
            telemetry.record_error(ErrorType.BAD_JSON)
            return []

        return known_commits

    def send_git_pack_file(self, packfile: Path) -> t.Optional[int]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="git_requests.objects_pack",
            duration="git_requests.objects_pack_ms",
            response_bytes=None,
            error="git_requests.objects_pack_errors",
        )

        try:
            metadata = {
                "data": {"id": self.env_tags[GitTag.COMMIT_SHA], "type": "commit"},
                "meta": {"repository_url": self.env_tags[GitTag.REPOSITORY_URL]},
            }

        except KeyError as e:
            log.warning("Git info not available, cannot send git packfile (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return None

        try:
            content = packfile.read_bytes()

            files = [
                FileAttachment(
                    name="pushedSha",
                    filename=None,
                    content_type="application/json",
                    data=json.dumps(metadata).encode("utf-8"),
                ),
                FileAttachment(
                    name="packfile", filename=packfile.name, content_type="application/octet-stream", data=content
                ),
            ]

        except Exception as e:
            log.warning("Error sending Git pack data: %s", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return None

        try:
            result = self.connector.post_files(
                "/api/v2/git/repository/packfile", files=files, send_gzip=False, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.warning("Failed to upload Git pack data: %s", e)
            return None

        return len(content)

    def get_skippable_tests(self) -> tuple[set[t.Union[SuiteRef, TestRef]], t.Optional[str]]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="itr_skippable_tests.request",
            duration="itr_skippable_tests.request_ms",
            response_bytes="itr_skippable_tests.response_bytes",
            error="itr_skippable_tests.request_errors",
        )

        try:
            request_data = {
                "data": {
                    "id": str(uuid.uuid4()),
                    "type": "test_params",
                    "attributes": {
                        "service": self.service,
                        "env": self.env,
                        "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                        "sha": self.env_tags[GitTag.COMMIT_SHA],
                        "configurations": self.configurations,
                        "test_level": self.itr_skipping_level.value,
                    },
                }
            }

        except KeyError as e:
            log.warning("Git info not available, cannot get skippable items (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return set(), None

        try:
            result = self.connector.post_json("/api/v2/ci/tests/skippable", request_data, telemetry=telemetry)
            result.on_error_raise_exception()

        except Exception as e:
            log.warning("Error getting skippable tests from API: %s", e)
            return set(), None

        try:
            skippable_items: set[t.Union[SuiteRef, TestRef]] = set()

            for item in result.parsed_response["data"]:
                if item["type"] in ("test", "suite"):
                    module_ref = ModuleRef(item["attributes"].get("configurations", {}).get("test.bundle", EMPTY_NAME))
                    suite_ref = SuiteRef(module_ref, item["attributes"].get("suite", EMPTY_NAME))
                    if item["type"] == "suite" and self.itr_skipping_level == ITRSkippingLevel.SUITE:
                        skippable_items.add(suite_ref)
                    elif item["type"] == "test" and self.itr_skipping_level == ITRSkippingLevel.TEST:
                        test_ref = TestRef(suite_ref, item["attributes"].get("name", EMPTY_NAME))
                        skippable_items.add(test_ref)

            correlation_id = result.parsed_response["meta"]["correlation_id"]

        except Exception as e:
            log.warning("Failed to parse skippable tests data from API: %s", e)
            telemetry.record_error(ErrorType.BAD_JSON)
            return set(), None

        self.telemetry_api.record_skippable_count(count=len(skippable_items), level=self.itr_skipping_level)

        return skippable_items, correlation_id

    def upload_coverage_report(
        self, coverage_report_bytes: bytes, coverage_format: str, tags: t.Optional[dict[str, str]] = None
    ) -> bool:
        """
        Upload a coverage report to Datadog CI Intake.

        Args:
            coverage_report_bytes: The coverage report content (will be gzipped)
            coverage_format: The format of the report (lcov, cobertura, jacoco, clover, opencover, simplecov)
            tags: Optional additional tags to include in the event

        Returns:
            True if upload succeeded, False otherwise
        """
        # Skip empty reports
        if not coverage_report_bytes:
            log.warning("Coverage report is empty, skipping upload")
            return False

        telemetry = self.telemetry_api.with_request_metric_names(
            count="coverage_upload.request",
            duration="coverage_upload.request_ms",
            response_bytes="coverage_upload.request_bytes",  # FIXME: Request bytes != response bytes
            error="coverage_upload.request_errors",
        )

        try:
            # Compress the coverage report with gzip
            compressed_report = gzip.compress(coverage_report_bytes)
            log.debug(
                "Compressed coverage report: %d bytes -> %d bytes", len(coverage_report_bytes), len(compressed_report)
            )

            # Warn if Git repository URL is missing
            if GitTag.REPOSITORY_URL not in self.env_tags:
                log.warning("Git repository URL not available for coverage report upload")

            # Create the event payload with git and CI tags
            from ddtrace.internal.test_visibility.coverage_report_utils import create_coverage_report_event

            all_tags = dict(self.env_tags)
            if tags:
                all_tags.update(tags)

            event_data = create_coverage_report_event(
                coverage_format=coverage_format,
                tags=all_tags,
            )

            # Debug log the event payload to diagnose 400 errors
            log.debug("Coverage report event payload: %s", json.dumps(event_data, indent=2))

            # Create multipart/form-data attachments
            # Filename format: coverage.{format}.gz (e.g., coverage.lcov.gz)
            files = [
                FileAttachment(
                    name="coverage",
                    filename=f"coverage.{coverage_format}.gz",
                    content_type="application/gzip",
                    data=compressed_report,
                ),
                FileAttachment(
                    name="event",
                    filename="event.json",
                    content_type="application/json",
                    data=json.dumps(event_data).encode("utf-8"),
                ),
            ]

            log.debug("Uploading coverage report: format=%s, size=%d bytes", coverage_format, len(compressed_report))

        except Exception as e:
            log.warning("Error preparing coverage report upload: %s", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return False

        try:
            result = self.coverage_connector.post_files(
                "/api/v2/cicovreprt", files=files, send_gzip=False, telemetry=telemetry
            )

            # Log response details for debugging
            if result.error_type:
                log.warning(
                    "Coverage report upload failed: error=%s, description=%s, response_body=%s",
                    result.error_type,
                    result.error_description,
                    result.response_body[:500] if result.response_body else None,
                )

            result.on_error_raise_exception()
            log.debug("Successfully uploaded coverage report")
            return True

        except Exception as e:
            log.warning("Failed to upload coverage report: %s", e)
            return False
