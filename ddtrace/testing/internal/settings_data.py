from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import logging
import typing as t

from ddtrace.internal.settings import env


log = logging.getLogger(__name__)


@dataclass
class EarlyFlakeDetectionSettings:
    enabled: bool = False
    slow_test_retries_5s: int = 10
    slow_test_retries_10s: int = 5
    slow_test_retries_30s: int = 3
    slow_test_retries_5m: int = 2
    faulty_session_threshold: int = 30

    @classmethod
    def from_attributes(cls, efd_attributes: dict[str, t.Any]) -> EarlyFlakeDetectionSettings:
        # Tolerate a partial response: each missing field falls back to its default instead of raising. A raise here
        # propagates up to Settings.from_attributes and is swallowed by APIClient.get_settings, which then returns a
        # default Settings() with *every* feature disabled — so one missing optional field would silently turn off all
        # of Test Optimization.
        defaults = cls()
        slow_test_retries = efd_attributes.get("slow_test_retries") or {}
        return cls(
            enabled=bool(efd_attributes.get("enabled", defaults.enabled)),
            slow_test_retries_5s=slow_test_retries.get("5s", defaults.slow_test_retries_5s),
            slow_test_retries_10s=slow_test_retries.get("10s", defaults.slow_test_retries_10s),
            slow_test_retries_30s=slow_test_retries.get("30s", defaults.slow_test_retries_30s),
            slow_test_retries_5m=slow_test_retries.get("5m", defaults.slow_test_retries_5m),
            faulty_session_threshold=efd_attributes.get("faulty_session_threshold", defaults.faulty_session_threshold),
        )


@dataclass
class AutoTestRetriesSettings:
    enabled: bool = False


@dataclass
class TestManagementSettings:
    __test__ = False
    enabled: bool = False
    attempt_to_fix_retries: int = 20

    @classmethod
    def from_attributes(cls, test_management_attributes: dict[str, t.Any]) -> TestManagementSettings:
        # Tolerate a partial response (see EarlyFlakeDetectionSettings.from_attributes for why).
        defaults = cls()
        attempt_to_fix_retries_env = env.get("DD_TEST_MANAGEMENT_ATTEMPT_TO_FIX_RETRIES")
        if attempt_to_fix_retries_env:
            if not attempt_to_fix_retries_env.isdigit():
                log.warning("Invalid number of Attempt to Fix retries set: %s", attempt_to_fix_retries_env)
                attempt_to_fix_retries = test_management_attributes.get(
                    "attempt_to_fix_retries", defaults.attempt_to_fix_retries
                )
                log.debug("Number of Attempt to Fix retries obtained from API: %d", attempt_to_fix_retries)
            else:
                attempt_to_fix_retries = int(attempt_to_fix_retries_env)
                log.debug("Number of Attempt to Fix retries obtained from environment: %d", attempt_to_fix_retries)
        else:
            attempt_to_fix_retries = test_management_attributes.get(
                "attempt_to_fix_retries", defaults.attempt_to_fix_retries
            )
            log.debug("Number of Attempt to Fix retries obtained from API: %d", attempt_to_fix_retries)
        return cls(
            enabled=bool(test_management_attributes.get("enabled", defaults.enabled)),
            attempt_to_fix_retries=attempt_to_fix_retries,
        )


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
    coverage_report_upload_enabled: bool = False

    @classmethod
    def from_attributes(cls, attributes: dict[str, t.Any]) -> Settings:
        # Default missing sections to an empty dict so a fully-absent section degrades to that feature's defaults
        # rather than passing None into the sub-parser (and raising).
        efd_attributes: dict[str, t.Any] = attributes.get("early_flake_detection") or {}
        test_management_attributes: dict[str, t.Any] = attributes.get("test_management") or {}
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
        coverage_report_upload_enabled = bool(attributes.get("coverage_report_upload_enabled"))

        settings = cls(
            early_flake_detection=efd_settings,
            test_management=test_management_settings,
            auto_test_retries=AutoTestRetriesSettings(enabled=atr_enabled),
            known_tests_enabled=known_tests_enabled,
            coverage_enabled=coverage_enabled,
            skipping_enabled=skipping_enabled,
            require_git=require_git,
            itr_enabled=itr_enabled,
            coverage_report_upload_enabled=coverage_report_upload_enabled,
        )

        return settings


@dataclass(frozen=True)
class TestProperties:
    quarantined: bool = False
    disabled: bool = False
    attempt_to_fix: bool = False
    active: bool = False

    __test__ = False
