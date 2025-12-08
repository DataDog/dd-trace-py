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
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestProperties
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
        self.connector = connector_setup.get_connector_for_subdomain("api")
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

        try:
            result = self.connector.post_json(
                "/api/v2/libraries/tests/services/setting", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()
            attributes = result.parsed_response["data"]["attributes"]
            settings = Settings.from_attributes(attributes)
            self.telemetry_api.record_settings(settings)
            return settings

        except Exception:
            log.exception("Error getting settings from API")
            return Settings()

    def get_known_tests(self) -> t.Set[TestRef]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="known_tests.request",
            duration="known_tests.request_ms",
            response_bytes="known_tests.response_bytes",
            error="known_tests.request_errors",
        )

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

        try:
            result = self.connector.post_json("/api/v2/ci/libraries/tests", request_data, telemetry=telemetry)
            result.on_error_raise_exception()
            tests_data = result.parsed_response["data"]["attributes"]["tests"]
            known_test_ids = set()

            for module, suites in tests_data.items():
                module_ref = ModuleRef(module)
                for suite, tests in suites.items():
                    suite_ref = SuiteRef(module_ref, suite)
                    for test in tests:
                        known_test_ids.add(TestRef(suite_ref, test))

            self.telemetry_api.record_known_tests_count(len(known_test_ids))
            return known_test_ids

        except Exception:
            log.exception("Error getting known tests from API")
            return set()

    def get_test_management_properties(self) -> t.Dict[TestRef, TestProperties]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="test_management_tests.request",
            duration="test_management_tests.request_ms",
            response_bytes="test_management_tests.response_bytes",
            error="test_management_tests.request_errors",
        )

        request_data = {
            "data": {
                "id": str(uuid.uuid4()),
                "type": "ci_app_libraries_tests_request",
                "attributes": {
                    "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
                    "commit_message": self.env_tags[GitTag.COMMIT_MESSAGE],
                    "sha": self.env_tags[GitTag.COMMIT_SHA],
                },
            }
        }

        try:
            result = self.connector.post_json(
                "/api/v2/test/libraries/test-management/tests", request_data, telemetry=telemetry
            )
            result.on_error_raise_exception()
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

            self.telemetry_api.record_test_management_tests_count(len(test_properties))

            return test_properties

        except Exception:
            log.exception("Failed to parse Test Management tests data")
            return {}

    def get_known_commits(self, latest_commits: t.List[str]) -> t.List[str]:
        request_data = {
            "meta": {
                "repository_url": self.env_tags[GitTag.REPOSITORY_URL],
            },
            "data": [{"id": sha, "type": "commit"} for sha in latest_commits],
        }

        try:
            result = self.connector.post_json("/api/v2/git/repository/search_commits", request_data)
            result.on_error_raise_exception()
            return [item["id"] for item in result.parsed_response["data"] if item["type"] == "commit"]

        except Exception:
            log.exception("Failed to parse search_commits data")
            return []

    def send_git_pack_file(self, packfile: Path) -> None:
        metadata = {
            "data": {"id": self.env_tags[GitTag.COMMIT_SHA], "type": "commit"},
            "meta": {"repository_url": self.env_tags[GitTag.REPOSITORY_URL]},
        }
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
        try:
            result = self.connector.post_files("/api/v2/git/repository/packfile", files=files, send_gzip=False)
            result.on_error_raise_exception()

        except Exception:
            log.warning("Failed to upload git pack data")

    def get_skippable_tests(self) -> t.Tuple[t.Set[t.Union[SuiteRef, TestRef]], t.Optional[str]]:
        telemetry = self.telemetry_api.with_request_metric_names(
            count="itr_skippable_tests.request",
            duration="itr_skippable_tests.request_ms",
            response_bytes="itr_skippable_tests.response_bytes",
            error="itr_skippable_tests.request_errors",
        )

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
        try:
            result = self.connector.post_json("/api/v2/ci/tests/skippable", request_data, telemetry=telemetry)
            result.on_error_raise_exception()

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

            self.telemetry_api.record_skippable_count(count=len(skippable_items), level=self.itr_skipping_level)

            return skippable_items, correlation_id

        except Exception:
            log.exception("Error getting skippable tests from API")
            return set(), None
