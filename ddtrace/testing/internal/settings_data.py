from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import typing as t


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
