from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
import time
import typing as t
import uuid

from ddtrace.testing.internal.ci import CITag
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


class APIClient:
    def __init__(
        self,
        service: str,
        env: str,
        env_tags: t.Dict[str, str],
        itr_skipping_level: ITRSkippingLevel,
        configurations: t.Dict[str, str],
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
            log.error("Git info not available, cannot fetch settings (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return Settings()

        try:
            result = self.connector.post_json(
                "/api/v2/libraries/tests/services/setting", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.error("Error getting settings from API: %s", e)
            return Settings()

        try:
            attributes = result.parsed_response["data"]["attributes"]
            settings = Settings.from_attributes(attributes)

        except Exception as e:
            log.exception("Error getting settings from API: %s", e)
            telemetry.record_error(ErrorType.BAD_JSON)
            return Settings()

        self.telemetry_api.record_settings(settings)
        return settings

    def get_known_tests(self) -> t.Set[TestRef]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="known_tests.request",
            duration="known_tests.request_ms",
            response_bytes="known_tests.response_bytes",
            error="known_tests.request_errors",
        )

        try:
            request_data: t.Dict[str, t.Any] = {
                "data": {
                    "id": str(uuid.uuid4()),
                    "type": "ci_app_libraries_tests_request",
                    "attributes": {
                        "service": self.service,
                        "env": self.env,
                        "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                        "configurations": self.configurations,
                    },
                }
            }

        except KeyError as e:
            log.error("Git info not available, cannot fetch known tests (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return set()

        try:
            result = self.connector.post_json("/api/v2/ci/libraries/tests", request_data, telemetry=telemetry)
            result.on_error_raise_exception()

        except Exception as e:
            log.exception("Error getting known tests from API: %s", e)
            return set()

        try:
            tests_data = result.parsed_response["data"]["attributes"]["tests"]
            known_test_ids = set()

            for module, suites in tests_data.items():
                module_ref = ModuleRef(module)
                for suite, tests in suites.items():
                    suite_ref = SuiteRef(module_ref, suite)
                    for test in tests:
                        known_test_ids.add(TestRef(suite_ref, test))

        except Exception:
            log.exception("Error getting known tests from API")
            telemetry.record_error(ErrorType.BAD_JSON)
            return set()

        self.telemetry_api.record_known_tests_count(len(known_test_ids))
        return known_test_ids

    def get_test_management_properties(self) -> t.Dict[TestRef, TestProperties]:
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
            log.error("Git info not available, cannot fetch Test Management properties (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return {}

        try:
            result = self.connector.post_json(
                "/api/v2/test/libraries/test-management/tests", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.error("Error getting Test Management properties from API: %s", e)
            return {}

        try:
            test_properties: t.Dict[TestRef, TestProperties] = {}
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

        except Exception:
            log.exception("Failed to parse Test Management tests data from API")
            telemetry.record_error(ErrorType.BAD_JSON)
            return {}

        self.telemetry_api.record_test_management_tests_count(len(test_properties))
        return test_properties

    def get_known_commits(self, latest_commits: t.List[str]) -> t.List[str]:
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
            log.error("Git info not available, cannot fetch known commits (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return []

        try:
            result = self.connector.post_json(
                "/api/v2/git/repository/search_commits", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception as e:
            log.error("Error getting known commits from API: %s", e)
            return []

        try:
            known_commits = [item["id"] for item in result.parsed_response["data"] if item["type"] == "commit"]

        except Exception:
            log.exception("Failed to parse search_commits data")
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
            log.error("Git info not available, cannot send git packfile (missing key: %s)", e)
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

        except Exception:
            log.exception("Error sending Git pack data")
            telemetry.record_error(ErrorType.UNKNOWN)
            return None

        try:
            result = self.connector.post_files(
                "/api/v2/git/repository/packfile", files=files, send_gzip=False, telemetry=telemetry
            )
            result.on_error_raise_exception()

        except Exception:
            log.warning("Failed to upload Git pack data")
            return None

        return len(content)

    def get_skippable_tests(self) -> t.Tuple[t.Set[t.Union[SuiteRef, TestRef]], t.Optional[str]]:
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
            log.error("Git info not available, cannot get skippable items (missing key: %s)", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return set(), None

        try:
            result = self.connector.post_json("/api/v2/ci/tests/skippable", request_data, telemetry=telemetry)
            result.on_error_raise_exception()

        except Exception as e:
            log.error("Error getting skippable tests from API: %s", e)
            return set(), None

        try:
            skippable_items: t.Set[t.Union[SuiteRef, TestRef]] = set()

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

        except Exception:
            log.exception("Failed to parse skippable tests data from API")
            telemetry.record_error(ErrorType.BAD_JSON)
            return set(), None

        self.telemetry_api.record_skippable_count(count=len(skippable_items), level=self.itr_skipping_level)

        return skippable_items, correlation_id

    def _create_coverage_report_event(
        self, coverage_format: str, tags: t.Optional[t.Dict[str, str]] = None
    ) -> t.Dict[str, t.Any]:
        """
        Create the event JSON for the coverage report upload with git and CI tags.

        Args:
            coverage_format: The format of the coverage report
            tags: Optional additional tags to include

        Returns:
            Event dictionary with type, format, and all available git/CI tags
        """
        event: t.Dict[str, t.Any] = {
            "type": "coverage_report",
            "format": coverage_format,
            "timestamp": int(time.time() * 1000),  # FIXME: Is this needed?
        }

        # Add any custom tags provided
        if tags:
            event.update(tags)

        # Add git tags from env_tags
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

        # Warn if Git repository URL is missing
        if GitTag.REPOSITORY_URL not in self.env_tags:
            log.warning("Git repository URL not available for coverage report upload")

        # Add CI tags from env_tags
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

    def upload_coverage_report(
        self, coverage_report_bytes: bytes, coverage_format: str, tags: t.Optional[t.Dict[str, str]] = None
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

            # Create the event payload with git and CI tags
            event_data = self._create_coverage_report_event(coverage_format, tags)

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
            log.exception("Error preparing coverage report upload: %s", e)
            telemetry.record_error(ErrorType.UNKNOWN)
            return False

        try:
            result = self.coverage_connector.post_files(
                "/api/v2/cicovreprt", files=files, send_gzip=False, telemetry=telemetry
            )

            # Log response details for debugging
            if result.error_type:
                log.error(
                    "Coverage report upload failed: error=%s, description=%s, response_body=%s",
                    result.error_type,
                    result.error_description,
                    result.response_body[:500] if result.response_body else None,
                )

            result.on_error_raise_exception()
            log.info("Successfully uploaded coverage report")
            return True

        except Exception as e:
            log.error("Failed to upload coverage report: %s", e)
            return False
