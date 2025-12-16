import json
import logging
from pathlib import Path
import typing as t
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch
import uuid

import pytest

from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendResult
from ddtrace.testing.internal.http import FileAttachment
from ddtrace.testing.internal.logging import testing_logger
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.test_data import ITRSkippingLevel
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef
from tests.testing.mocks import mock_backend_connector


@pytest.fixture(scope="module", autouse=True)
def override_testing_logger():
    # This is needed for the caplog fixture to work correctly, if previous tests have caused
    # `ddtrace.testing.internal.logging.setup_logging()` to be called.
    testing_logger.propagate = True


class TestAPIClientGetSettings:
    @pytest.mark.parametrize(
        "efd_enabled, atr_enabled, test_management_enabled, attempt_to_fix_retries, "
        "known_tests_enabled, coverage_enabled, skipping_enabled, require_git, itr_enabled",
        [
            (True, True, True, 30, True, True, True, True, True),
            (False, False, False, 40, False, False, False, False, False),
            (True, False, True, 50, True, False, True, False, True),
        ],
    )
    def test_get_settings(
        self,
        mock_telemetry: Mock,
        efd_enabled: bool,
        atr_enabled: bool,
        test_management_enabled: bool,
        attempt_to_fix_retries: int,
        known_tests_enabled: bool,
        coverage_enabled: bool,
        skipping_enabled: bool,
        require_git: bool,
        itr_enabled: bool,
    ) -> None:
        mock_connector = (
            mock_backend_connector()
            .with_post_json_response(
                endpoint="/api/v2/libraries/tests/services/setting",
                response_data={
                    "data": {
                        "attributes": {
                            "code_coverage": coverage_enabled,
                            "coverage_report_upload_enabled": False,
                            "di_enabled": False,
                            "early_flake_detection": {
                                "enabled": efd_enabled,
                                "faulty_session_threshold": 30,
                                "slow_test_retries": {"10s": 5, "30s": 3, "5m": 2, "5s": 10},
                            },
                            "flaky_test_retries_enabled": atr_enabled,
                            "impacted_tests_enabled": False,
                            "itr_enabled": itr_enabled,
                            "known_tests_enabled": known_tests_enabled,
                            "require_git": require_git,
                            "test_management": {
                                "attempt_to_fix_retries": attempt_to_fix_retries,
                                "enabled": test_management_enabled,
                            },
                            "tests_skipping": skipping_enabled,
                        },
                        "id": "00000000-0000-0000-0000-000000000000",
                        "type": "ci_app_tracers_test_service_settings",
                    }
                },
            )
            .build()
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            settings = api_client.get_settings()

        assert mock_connector.post_json.call_args_list == [
            call(
                "/api/v2/libraries/tests/services/setting",
                {
                    "data": {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "type": "ci_app_test_service_libraries_settings",
                        "attributes": {
                            "test_level": "test",
                            "service": "some-service",
                            "env": "some-env",
                            "repository_url": "http://github.com/DataDog/some-repo.git",
                            "sha": "abcd1234",
                            "branch": "some-branch",
                            "configurations": {"os.platform": "Linux"},
                        },
                    }
                },
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

        assert settings.early_flake_detection.enabled == efd_enabled
        assert settings.auto_test_retries.enabled == atr_enabled
        assert settings.test_management.enabled == test_management_enabled
        assert settings.test_management.attempt_to_fix_retries == attempt_to_fix_retries
        assert settings.known_tests_enabled == known_tests_enabled
        assert settings.coverage_enabled == coverage_enabled
        assert settings.skipping_enabled == skipping_enabled
        assert settings.require_git == require_git
        assert settings.itr_enabled == itr_enabled

    def test_get_settings_missing_git_data(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = mock_backend_connector().build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={},
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                settings = api_client.get_settings()

        assert "Git info not available" in caplog.text
        assert mock_connector.post_json.call_args_list == []

        assert settings.early_flake_detection.enabled is False
        assert settings.auto_test_retries.enabled is False
        assert settings.test_management.enabled is False
        assert settings.test_management.attempt_to_fix_retries == 20
        assert settings.known_tests_enabled is False
        assert settings.coverage_enabled is False
        assert settings.skipping_enabled is False
        assert settings.require_git is False
        assert settings.itr_enabled is False

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]

    def test_get_settings_fail_http_request(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = Mock()
        mock_connector.post_json.return_value = BackendResult(
            error_type=ErrorType.UNKNOWN, error_description="No can do"
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                settings = api_client.get_settings()

        assert "Error getting settings from API: No can do" in caplog.text

        assert settings.early_flake_detection.enabled is False
        assert settings.auto_test_retries.enabled is False
        assert settings.test_management.enabled is False
        assert settings.test_management.attempt_to_fix_retries == 20
        assert settings.known_tests_enabled is False
        assert settings.coverage_enabled is False
        assert settings.skipping_enabled is False
        assert settings.require_git is False
        assert settings.itr_enabled is False

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == []

    def test_get_settings_errors_in_response(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/libraries/tests/services/setting", response_data={"errors": "Weird stuff"}
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                settings = api_client.get_settings()

        assert "Error getting settings from API" in caplog.text
        assert "KeyError" in caplog.text

        assert settings.early_flake_detection.enabled is False
        assert settings.auto_test_retries.enabled is False
        assert settings.test_management.enabled is False
        assert settings.test_management.attempt_to_fix_retries == 20
        assert settings.known_tests_enabled is False
        assert settings.coverage_enabled is False
        assert settings.skipping_enabled is False
        assert settings.require_git is False
        assert settings.itr_enabled is False

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.BAD_JSON)
        ]


class TestAPIClientGetKnownTests:
    def test_get_known_tests(self, mock_telemetry: Mock) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/ci/libraries/tests",
                response_data={
                    "data": {
                        "attributes": {
                            "tests": {
                                "some-module": {
                                    "test_simple.py": ["test_01", "test_02"],
                                    "test_second.py": ["test_01", "test_02", "test_03"],
                                }
                            }
                        },
                        "id": "F4Go_FYpcB0",
                        "type": "ci_app_libraries_tests",
                    }
                },
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            known_tests = api_client.get_known_tests()

        assert mock_connector.post_json.call_args_list == [
            call(
                "/api/v2/ci/libraries/tests",
                {
                    "data": {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "type": "ci_app_libraries_tests_request",
                        "attributes": {
                            "service": "some-service",
                            "env": "some-env",
                            "repository_url": "http://github.com/DataDog/some-repo.git",
                            "configurations": {"os.platform": "Linux"},
                        },
                    }
                },
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

        assert known_tests == {
            TestRef(SuiteRef(ModuleRef("some-module"), "test_simple.py"), "test_01"),
            TestRef(SuiteRef(ModuleRef("some-module"), "test_simple.py"), "test_02"),
            TestRef(SuiteRef(ModuleRef("some-module"), "test_second.py"), "test_01"),
            TestRef(SuiteRef(ModuleRef("some-module"), "test_second.py"), "test_02"),
            TestRef(SuiteRef(ModuleRef("some-module"), "test_second.py"), "test_03"),
        }

    def test_get_known_tests_missing_git_data(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = mock_backend_connector().build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={},
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                known_tests = api_client.get_known_tests()

        assert "Git info not available" in caplog.text
        assert mock_connector.post_json.call_args_list == []

        assert known_tests == set()

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]

    def test_get_known_tests_fail_http_request(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = Mock()
        mock_connector.post_json.return_value = BackendResult(
            error_type=ErrorType.UNKNOWN, error_description="No can do"
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                known_tests = api_client.get_known_tests()

        assert "Error getting known tests from API: No can do" in caplog.text

        assert known_tests == set()
        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == []

    def test_get_known_tests_errors_in_response(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/ci/libraries/tests", response_data={"errors": "Weird stuff"}
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                known_tests = api_client.get_known_tests()

        assert "Error getting known tests from API" in caplog.text
        assert "KeyError" in caplog.text

        assert known_tests == set()

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.BAD_JSON)
        ]


class TestAPIClientGetTestManagementTests:
    RESPONSE_DATA = {
        "data": {
            "attributes": {
                "modules": {
                    "some_module": {
                        "suites": {
                            "first.py": {
                                "tests": {
                                    "test_01": {
                                        "properties": {
                                            "attempt_to_fix": False,
                                            "disabled": False,
                                            "quarantined": True,
                                        }
                                    }
                                }
                            },
                            "second.py": {
                                "tests": {
                                    "test_02": {
                                        "properties": {
                                            "attempt_to_fix": False,
                                            "disabled": True,
                                            "quarantined": False,
                                        }
                                    }
                                }
                            },
                        }
                    }
                }
            },
            "id": "e7e4d0b95cb68806",
            "type": "ci_app_libraries_tests",
        }
    }

    def test_get_test_management_tests(self, mock_telemetry: Mock) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/test/libraries/test-management/tests",
                response_data=self.RESPONSE_DATA,
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            properties = api_client.get_test_management_properties()

        assert mock_connector.post_json.call_args_list == [
            call(
                "/api/v2/test/libraries/test-management/tests",
                {
                    "data": {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "type": "ci_app_libraries_tests_request",
                        "attributes": {
                            "repository_url": "http://github.com/DataDog/some-repo.git",
                            "commit_message": "I am a commit",
                            "sha": "abcd1234",
                        },
                    }
                },
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

        assert properties == {
            TestRef(SuiteRef(ModuleRef("some_module"), "first.py"), "test_01"): TestProperties(
                quarantined=True, disabled=False, attempt_to_fix=False
            ),
            TestRef(SuiteRef(ModuleRef("some_module"), "second.py"), "test_02"): TestProperties(
                quarantined=False, disabled=True, attempt_to_fix=False
            ),
        }

    def test_get_test_management_tests_use_head_commit_data_if_available(self, mock_telemetry: Mock) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/test/libraries/test-management/tests",
                response_data=self.RESPONSE_DATA,
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
                GitTag.COMMIT_HEAD_SHA: "8ead8ead",
                GitTag.COMMIT_HEAD_MESSAGE: "I am the head commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            properties = api_client.get_test_management_properties()

        assert mock_connector.post_json.call_args_list == [
            call(
                "/api/v2/test/libraries/test-management/tests",
                {
                    "data": {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "type": "ci_app_libraries_tests_request",
                        "attributes": {
                            "repository_url": "http://github.com/DataDog/some-repo.git",
                            "commit_message": "I am the head commit",
                            "sha": "8ead8ead",
                        },
                    }
                },
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

        assert properties == {
            TestRef(SuiteRef(ModuleRef("some_module"), "first.py"), "test_01"): TestProperties(
                quarantined=True, disabled=False, attempt_to_fix=False
            ),
            TestRef(SuiteRef(ModuleRef("some_module"), "second.py"), "test_02"): TestProperties(
                quarantined=False, disabled=True, attempt_to_fix=False
            ),
        }

    def test_get_test_management_properties_missing_git_data(
        self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_connector = mock_backend_connector().build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={},
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                properties = api_client.get_test_management_properties()

        assert "Git info not available" in caplog.text
        assert mock_connector.post_json.call_args_list == []

        assert properties == {}

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]

    def test_get_test_management_properties_fail_http_request(
        self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_connector = Mock()
        mock_connector.post_json.return_value = BackendResult(
            error_type=ErrorType.UNKNOWN, error_description="No can do"
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                properties = api_client.get_test_management_properties()

        assert "Error getting Test Management properties from API" in caplog.text

        assert properties == {}
        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == []

    def test_get_test_management_tests_errors_in_response(
        self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/test/libraries/test-management/tests", response_data={"errors": "Weird stuff"}
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                properties = api_client.get_test_management_properties()

        assert "Failed to parse Test Management tests data from API" in caplog.text
        assert "KeyError" in caplog.text

        assert properties == {}

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.BAD_JSON)
        ]


class TestAPIClientGetKnownCommits:
    def test_get_known_commits(self, mock_telemetry: Mock) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/git/repository/search_commits",
                response_data={"data": [{"id": "abcd0123", "type": "commit"}, {"id": "dcba4321", "type": "commit"}]},
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            commits = api_client.get_known_commits(latest_commits=["0000abcd", "1111abcd"])

        assert mock_connector.post_json.call_args_list == [
            call(
                "/api/v2/git/repository/search_commits",
                {
                    "meta": {"repository_url": "http://github.com/DataDog/some-repo.git"},
                    "data": [{"id": "0000abcd", "type": "commit"}, {"id": "1111abcd", "type": "commit"}],
                },
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

        assert commits == ["abcd0123", "dcba4321"]

    def test_get_known_commits_missing_git_data(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = mock_backend_connector().build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={},
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                commits = api_client.get_known_commits(latest_commits=["0000abcd", "1111abcd"])

        assert "Git info not available" in caplog.text
        assert mock_connector.post_json.call_args_list == []

        assert commits == []

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]

    def test_get_known_commits_fail_http_request(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = Mock()
        mock_connector.post_json.return_value = BackendResult(
            error_type=ErrorType.UNKNOWN, error_description="No can do"
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                commits = api_client.get_known_commits(latest_commits=["0000abcd", "1111abcd"])

        assert "Error getting known commits from API" in caplog.text

        assert commits == []
        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == []

    def test_get_known_commits_errors_in_response(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/git/repository/search_commits", response_data={"errors": "Weird stuff"}
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                commits = api_client.get_known_commits(latest_commits=["0000abcd", "1111abcd"])

        assert "Failed to parse search_commits data" in caplog.text
        assert "KeyError" in caplog.text

        assert commits == []

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.BAD_JSON)
        ]


class TestAPIClientGetSkippableTests:
    def test_get_skippable_tests(self, mock_telemetry: Mock) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/ci/tests/skippable",
                response_data={
                    "data": [
                        {
                            "attributes": {
                                "configurations": {"test.bundle": "tests"},
                                "name": "test_01",
                                "suite": "test_simple.py",
                            },
                            "id": "b64c9cec67b328f2",
                            "type": "test",
                        },
                        {
                            "attributes": {
                                "configurations": {"test.bundle": "tests"},
                                "name": "test_02",
                                "suite": "test_second.py",
                            },
                            "id": "87197af576c002b3",
                            "type": "test",
                        },
                    ],
                    "meta": {
                        "correlation_id": "8ac307ca693b2ffd365ab2c3b47cb555",
                        "coverage": {
                            "tests/test_second.py": "AAABpAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                            "tests/test_simple.py": "XYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        },
                    },
                },
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            skippable_tests, correlation_id = api_client.get_skippable_tests()

        assert mock_connector.post_json.call_args_list == [
            call(
                "/api/v2/ci/tests/skippable",
                {
                    "data": {
                        "id": "00000000-0000-0000-0000-000000000000",
                        "type": "test_params",
                        "attributes": {
                            "service": "some-service",
                            "env": "some-env",
                            "repository_url": "http://github.com/DataDog/some-repo.git",
                            "sha": "abcd1234",
                            "configurations": {"os.platform": "Linux"},
                            "test_level": "test",
                        },
                    }
                },
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

        assert skippable_tests == {
            TestRef(SuiteRef(ModuleRef("tests"), "test_simple.py"), "test_01"),
            TestRef(SuiteRef(ModuleRef("tests"), "test_second.py"), "test_02"),
        }
        assert correlation_id == "8ac307ca693b2ffd365ab2c3b47cb555"

    def test_get_skippable_tests_missing_git_data(self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture) -> None:
        mock_connector = mock_backend_connector().build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={},
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                skippable_tests, correlation_id = api_client.get_skippable_tests()

        assert "Git info not available" in caplog.text
        assert mock_connector.post_json.call_args_list == []

        assert skippable_tests == set()
        assert correlation_id is None

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]

    def test_get_skippable_tests_fail_http_request(
        self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_connector = Mock()
        mock_connector.post_json.return_value = BackendResult(
            error_type=ErrorType.UNKNOWN, error_description="No can do"
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                skippable_tests, correlation_id = api_client.get_skippable_tests()

        assert "Error getting skippable tests from API" in caplog.text

        assert skippable_tests == set()
        assert correlation_id is None

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == []

    def test_get_skippable_tests_errors_in_response(
        self, mock_telemetry: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/ci/tests/skippable", response_data={"errors": "Weird stuff"}
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                skippable_tests, correlation_id = api_client.get_skippable_tests()

        assert "Failed to parse skippable tests data" in caplog.text
        assert "KeyError" in caplog.text

        assert skippable_tests == set()
        assert correlation_id is None

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.BAD_JSON)
        ]


@pytest.fixture
def packfile(tmpdir: t.Any) -> Path:
    path = Path(str(tmpdir)) / "file.pack"
    path.write_text("twelve bytes")
    yield path


class TestAPIClientSendGitPackfile:
    def test_send_git_pack_file(self, mock_telemetry: Mock, packfile: Path) -> None:
        mock_connector = Mock()
        mock_connector.post_files.return_value = BackendResult(response=Mock(status=200))

        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            api_client.send_git_pack_file(packfile)

        assert mock_connector.post_files.call_args_list == [
            call(
                "/api/v2/git/repository/packfile",
                files=[
                    FileAttachment(
                        name="pushedSha",
                        filename=None,
                        content_type="application/json",
                        data=json.dumps(
                            {
                                "data": {"id": "abcd1234", "type": "commit"},
                                "meta": {"repository_url": "http://github.com/DataDog/some-repo.git"},
                            }
                        ).encode("utf-8"),
                    ),
                    FileAttachment(
                        name="packfile",
                        filename="file.pack",
                        content_type="application/octet-stream",
                        data=b"twelve bytes",
                    ),
                ],
                send_gzip=False,
                telemetry=mock_telemetry.with_request_metric_names.return_value,
            )
        ]

    def test_send_git_pack_file_missing_git_data(
        self,
        mock_telemetry: Mock,
        caplog: pytest.LogCaptureFixture,
        packfile: Path,
    ) -> None:
        mock_connector = mock_backend_connector().build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={},
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                api_client.send_git_pack_file(packfile)

        assert "Git info not available" in caplog.text

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]

    def test_send_git_pack_file_fail_http_request(
        self,
        mock_telemetry: Mock,
        caplog: pytest.LogCaptureFixture,
        packfile: Path,
    ) -> None:
        mock_connector = Mock()
        mock_connector.post_files.return_value = BackendResult(
            error_type=ErrorType.UNKNOWN, error_description="No can do"
        )
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                api_client.send_git_pack_file(packfile)

        assert "Failed to upload Git pack data" in caplog.text

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == []

    def test_send_git_pack_file_errors_in_reading(
        self,
        mock_telemetry: Mock,
        caplog: pytest.LogCaptureFixture,
        tmpdir: t.Any,
    ) -> None:
        mock_connector = (
            mock_backend_connector().with_post_json_response(
                endpoint="/api/v2/ci/tests/skippable", response_data={"errors": "Weird stuff"}
            )
        ).build()
        mock_connector_setup = Mock()
        mock_connector_setup.get_connector_for_subdomain.return_value = mock_connector

        api_client = APIClient(
            service="some-service",
            env="some-env",
            env_tags={
                GitTag.REPOSITORY_URL: "http://github.com/DataDog/some-repo.git",
                GitTag.COMMIT_SHA: "abcd1234",
                GitTag.BRANCH: "some-branch",
                GitTag.COMMIT_MESSAGE: "I am a commit",
            },
            itr_skipping_level=ITRSkippingLevel.TEST,
            configurations={
                "os.platform": "Linux",
            },
            connector_setup=mock_connector_setup,
            telemetry_api=mock_telemetry,
        )

        with patch("uuid.uuid4", return_value=uuid.UUID("00000000-0000-0000-0000-000000000000")):
            with caplog.at_level(level=logging.INFO, logger="ddtrace.testing"):
                api_client.send_git_pack_file(Path(tmpdir) / "non_existent_file.pack")

        assert "Error sending Git pack data" in caplog.text

        assert mock_telemetry.with_request_metric_names.return_value.record_error.call_args_list == [
            call(ErrorType.UNKNOWN)
        ]
