"""
File-based data provider for Bazel offline (manifest) mode.

Defines the ``TestOptDataProvider`` Protocol that both ``APIClient`` and
``CachedFileDataProvider`` satisfy, so ``SessionManager`` can swap between
HTTP and file-based data fetching without branching inside each method.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import typing as t

from ddtrace.testing.internal.constants import ITRSkippingLevel
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


log = logging.getLogger(__name__)


class TestOptDataProvider(t.Protocol):
    """
    Protocol satisfied by both ``APIClient`` (HTTP) and ``CachedFileDataProvider`` (files).

    ``SessionManager`` types its ``api_client`` attribute as this Protocol so
    mypy catches interface drift between the two implementations.
    """

    configuration_errors: dict[str, str]

    def get_settings(self) -> Settings: ...

    def get_known_tests(self) -> set[TestRef]: ...

    def get_test_management_properties(self) -> dict[TestRef, TestProperties]: ...

    def get_skippable_tests(self) -> tuple[set[t.Union[SuiteRef, TestRef]], t.Optional[str]]: ...

    def get_known_commits(self, latest_commits: list[str]) -> t.Optional[list[str]]: ...

    def send_git_pack_file(self, packfile: Path) -> t.Optional[int]: ...

    def upload_coverage_report(
        self,
        coverage_report_bytes: bytes,
        coverage_format: str,
        tags: t.Optional[dict[str, str]],
    ) -> bool: ...

    def close(self) -> None: ...


def _read_cache_json(cache_path: str) -> t.Optional[t.Any]:
    """
    Read and parse a JSON file from the .testoptimization cache directory.

    Returns the parsed object on success, or None if the file is missing or
    unreadable. A missing file is treated as an empty response — no HTTP
    fallback is attempted (Bazel hermeticity requires this hard boundary).
    """
    try:
        with open(cache_path) as f:
            return json.load(f)
    except FileNotFoundError:
        log.debug("Cache file not found: %s — treating as empty response", cache_path)
        return None
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Error reading cache file %s: %s — treating as empty response", cache_path, e)
        return None


class CachedFileDataProvider:
    """
    Reads test optimization data from pre-fetched JSON files in the
    .testoptimization directory (Bazel manifest mode).

    All four fetch methods mirror the structure of the corresponding backend
    HTTP responses so the same parsing code applies. Methods that are only
    reachable via ``upload_git_data`` (which is already guarded to skip in
    offline mode) are implemented as no-ops.
    """

    def __init__(
        self,
        test_optimization_dir: str,
        itr_skipping_level: ITRSkippingLevel,
        telemetry_api: TelemetryAPI,
    ) -> None:
        self._dir = test_optimization_dir
        self._itr_skipping_level = itr_skipping_level
        self._telemetry_api = telemetry_api
        self.configuration_errors: dict[str, str] = {}

    def _cache_path(self, relative: str) -> str:
        return os.path.join(self._dir, *relative.split("/"))

    def get_settings(self) -> Settings:
        cached = _read_cache_json(self._cache_path("cache/http/settings.json"))
        if cached is None:
            log.debug("No cached settings file — all features disabled in manifest mode")
            return Settings()
        try:
            settings = Settings.from_attributes(cached["data"]["attributes"])
        except Exception as e:
            log.warning("Error parsing cached settings file: %s — all features disabled", e)
            return Settings()
        self._telemetry_api.record_settings(settings)
        return settings

    def get_known_tests(self) -> set[TestRef]:
        cached = _read_cache_json(self._cache_path("cache/http/known_tests.json"))
        if cached is None:
            return set()
        try:
            known: set[TestRef] = set()
            for module, suites in cached["data"]["attributes"]["tests"].items():
                module_ref = ModuleRef(module)
                for suite, tests in suites.items():
                    suite_ref = SuiteRef(module_ref, suite)
                    for test in tests:
                        known.add(TestRef(suite_ref, test))
            self._telemetry_api.record_known_tests_count(len(known))
            return known
        except Exception as e:
            log.warning("Error parsing cached known tests file: %s", e)
            return set()

    def get_test_management_properties(self) -> dict[TestRef, TestProperties]:
        cached = _read_cache_json(self._cache_path("cache/http/test_management.json"))
        if cached is None:
            return {}
        try:
            props: dict[TestRef, TestProperties] = {}
            for module_name, module_data in cached["data"]["attributes"]["modules"].items():
                module_ref = ModuleRef(module_name)
                for suite_name, suite_data in module_data["suites"].items():
                    suite_ref = SuiteRef(module_ref, suite_name)
                    for test_name, test_data in suite_data["tests"].items():
                        p = test_data.get("properties", {})
                        props[TestRef(suite_ref, test_name)] = TestProperties(
                            quarantined=p.get("quarantined", False),
                            disabled=p.get("disabled", False),
                            attempt_to_fix=p.get("attempt_to_fix", False),
                        )
            self._telemetry_api.record_test_management_tests_count(len(props))
            return props
        except Exception as e:
            log.warning("Error parsing cached test management file: %s", e)
            return {}

    def get_skippable_tests(self) -> tuple[set[t.Union[SuiteRef, TestRef]], t.Optional[str]]:
        # Hard no-op in manifest mode: skippable tests are not applied in hermetic
        # Bazel runs. This matches the Go implementation which returns an empty set
        # without reading the cache file.
        return set(), None

    # --- no-ops for methods unreachable in manifest mode ---

    def get_known_commits(self, latest_commits: list[str]) -> list[str]:
        return []  # upload_git_data() returns early in manifest mode

    def send_git_pack_file(self, packfile: Path) -> t.Optional[int]:
        return None  # upload_git_data() returns early in manifest mode

    def upload_coverage_report(
        self,
        coverage_report_bytes: bytes,
        coverage_format: str,
        tags: t.Optional[dict[str, str]] = None,
    ) -> bool:
        return False  # coverage upload is skipped in payload-files mode

    def close(self) -> None:
        pass
