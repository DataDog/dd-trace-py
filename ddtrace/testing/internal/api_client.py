from __future__ import annotations

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
        self.telemetry_api = telemetry_api

    def close(self) -> None:
        self.connector.close()

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
            request_data = {
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
