from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import json
import logging
from pathlib import Path
import typing as t
import uuid

from ddtrace.testing.internal.constants import EMPTY_NAME
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.test_data import ITRSkippingLevel
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.telemetry import TelemetryAPI


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
        telemetry = self.telemetry_api.request_metrics(
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
            response, response_data = self.connector.post_json("/api/v2/libraries/tests/services/setting", request_data, telemetry=telemetry)
            attributes = response_data["data"]["attributes"]
            return Settings.from_attributes(attributes)

        except Exception:
            log.exception("Error getting settings from API")
            return Settings()

    def get_known_tests(self) -> t.Set[TestRef]:
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
            response, response_data = self.connector.post_json("/api/v2/ci/libraries/tests", request_data)
            tests_data = response_data["data"]["attributes"]["tests"]
            known_test_ids = set()

            for module, suites in tests_data.items():
                module_ref = ModuleRef(module)
                for suite, tests in suites.items():
                    suite_ref = SuiteRef(module_ref, suite)
                    for test in tests:
                        known_test_ids.add(TestRef(suite_ref, test))

            return known_test_ids

        except Exception:
            log.exception("Error getting known tests from API")
            return set()

    def get_test_management_properties(self) -> t.Dict[TestRef, TestProperties]:
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
            response, response_data = self.connector.post_json(
                "/api/v2/test/libraries/test-management/tests", request_data
            )
            test_properties: t.Dict[TestRef, TestProperties] = {}
            modules = response_data["data"]["attributes"]["modules"]

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
            response, response_data = self.connector.post_json("/api/v2/git/repository/search_commits", request_data)
            return [item["id"] for item in response_data["data"] if item["type"] == "commit"]

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
        response, response_data = self.connector.post_files(
            "/api/v2/git/repository/packfile", files=files, send_gzip=False
        )

        if response.status != 204:
            log.warning("Failed to upload git pack data: %s %s", response.status, response_data)

    def get_skippable_tests(self) -> t.Tuple[t.Set[t.Union[SuiteRef, TestRef]], t.Optional[str]]:
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
            response, response_data = self.connector.post_json("/api/v2/ci/tests/skippable", request_data)
            skippable_items: t.Set[t.Union[SuiteRef, TestRef]] = set()

            for item in response_data["data"]:
                if item["type"] in ("test", "suite"):
                    module_ref = ModuleRef(item["attributes"].get("configurations", {}).get("test.bundle", EMPTY_NAME))
                    suite_ref = SuiteRef(module_ref, item["attributes"].get("suite", EMPTY_NAME))
                    if item["type"] == "suite" and self.itr_skipping_level == ITRSkippingLevel.SUITE:
                        skippable_items.add(suite_ref)
                    elif item["type"] == "test" and self.itr_skipping_level == ITRSkippingLevel.TEST:
                        test_ref = TestRef(suite_ref, item["attributes"].get("name", EMPTY_NAME))
                        skippable_items.add(test_ref)

            correlation_id = response_data["meta"]["correlation_id"]

            return skippable_items, correlation_id

        except Exception:
            log.exception("Error getting skippable tests from API")
            return set(), None


@dataclass
class EarlyFlakeDetectionSettings:
    enabled: bool = False
    slow_test_retries_5s: int = 10
    slow_test_retries_10s: int = 5
    slow_test_retries_30s: int = 3
    slow_test_retries_5m: int = 2
    faulty_session_threshold: int = 30

    @classmethod
    def from_attributes(cls, efd_attributes: t.Dict[str, t.Any]) -> EarlyFlakeDetectionSettings:
        efd_settings = cls(
            enabled=efd_attributes["enabled"],
            slow_test_retries_5s=efd_attributes["slow_test_retries"]["5s"],
            slow_test_retries_10s=efd_attributes["slow_test_retries"]["10s"],
            slow_test_retries_30s=efd_attributes["slow_test_retries"]["30s"],
            slow_test_retries_5m=efd_attributes["slow_test_retries"]["5m"],
            faulty_session_threshold=efd_attributes["faulty_session_threshold"],
        )
        return efd_settings


@dataclass
class AutoTestRetriesSettings:
    enabled: bool = False


@dataclass
class TestManagementSettings:
    __test__ = False
    enabled: bool = False
    attempt_to_fix_retries: int = 20

    @classmethod
    def from_attributes(cls, test_management_attributes: t.Dict[str, t.Any]) -> TestManagementSettings:
        test_management_settings = cls(
            enabled=test_management_attributes["enabled"],
            attempt_to_fix_retries=test_management_attributes["attempt_to_fix_retries"],
        )
        return test_management_settings


@dataclass
class Settings:
    early_flake_detection: EarlyFlakeDetectionSettings = field(default_factory=EarlyFlakeDetectionSettings)
    auto_test_retries: AutoTestRetriesSettings = field(default_factory=AutoTestRetriesSettings)
    test_management: TestManagementSettings = field(default_factory=TestManagementSettings)
    known_tests_enabled: bool = False

    coverage_enabled: bool = False
    skipping_enabled: bool = False
    require_git: bool = False
    itr_enabled: bool = False

    @classmethod
    def from_attributes(cls, attributes: t.Dict[str, t.Any]) -> Settings:
        efd_attributes: t.Dict[str, t.Any] = t.cast(t.Dict[str, t.Any], attributes.get("early_flake_detection"))
        test_management_attributes: t.Dict[str, t.Any] = t.cast(t.Dict[str, t.Any], attributes.get("test_management"))
        efd_settings = EarlyFlakeDetectionSettings.from_attributes(efd_attributes)
        test_management_settings = TestManagementSettings.from_attributes(
            test_management_attributes=test_management_attributes
        )
        atr_enabled = bool(attributes.get("flaky_test_retries_enabled"))
        known_tests_enabled = bool(attributes.get("known_tests_enabled"))
        coverage_enabled = bool(attributes.get("code_coverage"))
        skipping_enabled = bool(attributes.get("tests_skipping"))
        require_git = bool(attributes.get("require_git"))
        itr_enabled = bool(attributes.get("itr_enabled"))

        settings = cls(
            early_flake_detection=efd_settings,
            test_management=test_management_settings,
            auto_test_retries=AutoTestRetriesSettings(enabled=atr_enabled),
            known_tests_enabled=known_tests_enabled,
            coverage_enabled=coverage_enabled,
            skipping_enabled=skipping_enabled,
            require_git=require_git,
            itr_enabled=itr_enabled,
        )

        return settings


@dataclass(frozen=True)
class TestProperties:
    quarantined: bool = False
    disabled: bool = False
    attempt_to_fix: bool = False

    __test__ = False
