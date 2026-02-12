from __future__ import annotations

# Removed dataclass import - using __slots__ for memory efficiency
import typing as t


class EarlyFlakeDetectionSettings:
    """Lightweight __slots__ version of EarlyFlakeDetectionSettings."""

    __slots__ = (
        "enabled",
        "slow_test_retries_5s",
        "slow_test_retries_10s",
        "slow_test_retries_30s",
        "slow_test_retries_5m",
        "faulty_session_threshold",
    )

    def __init__(
        self,
        enabled: bool = False,
        slow_test_retries_5s: int = 10,
        slow_test_retries_10s: int = 5,
        slow_test_retries_30s: int = 3,
        slow_test_retries_5m: int = 2,
        faulty_session_threshold: int = 30,
    ):
        self.enabled = enabled
        self.slow_test_retries_5s = slow_test_retries_5s
        self.slow_test_retries_10s = slow_test_retries_10s
        self.slow_test_retries_30s = slow_test_retries_30s
        self.slow_test_retries_5m = slow_test_retries_5m
        self.faulty_session_threshold = faulty_session_threshold

    @classmethod
    def from_attributes(cls, efd_attributes: t.Dict[str, t.Any]) -> "EarlyFlakeDetectionSettings":
        efd_settings = cls(
            enabled=efd_attributes["enabled"],
            slow_test_retries_5s=efd_attributes["slow_test_retries"]["5s"],
            slow_test_retries_10s=efd_attributes["slow_test_retries"]["10s"],
            slow_test_retries_30s=efd_attributes["slow_test_retries"]["30s"],
            slow_test_retries_5m=efd_attributes["slow_test_retries"]["5m"],
            faulty_session_threshold=efd_attributes["faulty_session_threshold"],
        )
        return efd_settings

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EarlyFlakeDetectionSettings):
            return NotImplemented
        return (
            self.enabled == other.enabled
            and self.slow_test_retries_5s == other.slow_test_retries_5s
            and self.slow_test_retries_10s == other.slow_test_retries_10s
            and self.slow_test_retries_30s == other.slow_test_retries_30s
            and self.slow_test_retries_5m == other.slow_test_retries_5m
            and self.faulty_session_threshold == other.faulty_session_threshold
        )


class AutoTestRetriesSettings:
    """Lightweight __slots__ version of AutoTestRetriesSettings."""

    __slots__ = ("enabled",)

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AutoTestRetriesSettings):
            return NotImplemented
        return self.enabled == other.enabled


class TestManagementSettings:
    """Lightweight __slots__ version of TestManagementSettings."""

    __slots__ = ("enabled", "attempt_to_fix_retries")
    __test__ = False

    def __init__(self, enabled: bool = False, attempt_to_fix_retries: int = 20):
        self.enabled = enabled
        self.attempt_to_fix_retries = attempt_to_fix_retries

    @classmethod
    def from_attributes(cls, test_management_attributes: t.Dict[str, t.Any]) -> "TestManagementSettings":
        test_management_settings = cls(
            enabled=test_management_attributes["enabled"],
            attempt_to_fix_retries=test_management_attributes["attempt_to_fix_retries"],
        )
        return test_management_settings

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TestManagementSettings):
            return NotImplemented
        return self.enabled == other.enabled and self.attempt_to_fix_retries == other.attempt_to_fix_retries


class Settings:
    """Lightweight __slots__ version of Settings."""

    __slots__ = (
        "early_flake_detection",
        "auto_test_retries",
        "test_management",
        "known_tests_enabled",
        "coverage_enabled",
        "skipping_enabled",
        "require_git",
        "itr_enabled",
        "coverage_report_upload_enabled",
    )

    def __init__(
        self,
        early_flake_detection: t.Optional[EarlyFlakeDetectionSettings] = None,
        auto_test_retries: t.Optional[AutoTestRetriesSettings] = None,
        test_management: t.Optional[TestManagementSettings] = None,
        known_tests_enabled: bool = False,
        coverage_enabled: bool = False,
        skipping_enabled: bool = False,
        require_git: bool = False,
        itr_enabled: bool = False,
        coverage_report_upload_enabled: bool = False,
    ):
        self.early_flake_detection = early_flake_detection or EarlyFlakeDetectionSettings()
        self.auto_test_retries = auto_test_retries or AutoTestRetriesSettings()
        self.test_management = test_management or TestManagementSettings()
        self.known_tests_enabled = known_tests_enabled
        self.coverage_enabled = coverage_enabled
        self.skipping_enabled = skipping_enabled
        self.require_git = require_git
        self.itr_enabled = itr_enabled
        self.coverage_report_upload_enabled = coverage_report_upload_enabled

    @classmethod
    def from_attributes(cls, attributes: t.Dict[str, t.Any]) -> "Settings":
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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Settings):
            return NotImplemented
        return (
            self.early_flake_detection == other.early_flake_detection
            and self.auto_test_retries == other.auto_test_retries
            and self.test_management == other.test_management
            and self.known_tests_enabled == other.known_tests_enabled
            and self.coverage_enabled == other.coverage_enabled
            and self.skipping_enabled == other.skipping_enabled
            and self.require_git == other.require_git
            and self.itr_enabled == other.itr_enabled
            and self.coverage_report_upload_enabled == other.coverage_report_upload_enabled
        )


class TestProperties:
    """Lightweight __slots__ version of TestProperties (immutable)."""

    __slots__ = ("_quarantined", "_disabled", "_attempt_to_fix")
    __test__ = False

    def __init__(self, quarantined: bool = False, disabled: bool = False, attempt_to_fix: bool = False):
        # Use private attributes to simulate frozen behavior
        object.__setattr__(self, "_quarantined", quarantined)
        object.__setattr__(self, "_disabled", disabled)
        object.__setattr__(self, "_attempt_to_fix", attempt_to_fix)

    @property
    def quarantined(self) -> bool:
        return self._quarantined

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def attempt_to_fix(self) -> bool:
        return self._attempt_to_fix

    def __setattr__(self, name: str, value: t.Any) -> None:
        if hasattr(self, "_quarantined"):  # Already initialized
            raise AttributeError(f"can't set attribute '{name}' on frozen TestProperties")
        super().__setattr__(name, value)
