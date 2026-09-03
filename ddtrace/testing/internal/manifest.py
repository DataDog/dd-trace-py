"""Helpers for writing Test Optimization manifest-mode caches."""

from __future__ import annotations

import json
from pathlib import Path
import typing as t

from ddtrace.testing.internal.constants import SUPPORTED_MANIFEST_VERSION
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_HTTP_CACHE_DIR
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_KNOWN_TESTS_FILE
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_MANIFEST_FILENAME
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_SETTINGS_FILE
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_SKIPPABLE_TESTS_FILE
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_TEST_MANAGEMENT_FILE
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import TestRef


def manifest_file_path(test_optimization_dir: Path) -> Path:
    return test_optimization_dir / TEST_OPTIMIZATION_MANIFEST_FILENAME


def write_manifest_cache(
    test_optimization_dir: Path,
    settings: Settings,
    known_tests: set[TestRef],
    test_properties: dict[TestRef, TestProperties],
    skippable_items: set[t.Union[SuiteRef, TestRef]],
    itr_correlation_id: t.Optional[str],
) -> Path:
    """Write a manifest-mode cache and return its manifest file path."""
    http_dir = test_optimization_dir / TEST_OPTIMIZATION_HTTP_CACHE_DIR
    http_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_file_path(test_optimization_dir)
    _write_manifest_json(http_dir / TEST_OPTIMIZATION_SETTINGS_FILE, settings_manifest_response(settings))
    _write_manifest_json(http_dir / TEST_OPTIMIZATION_KNOWN_TESTS_FILE, known_tests_manifest_response(known_tests))
    _write_manifest_json(
        http_dir / TEST_OPTIMIZATION_TEST_MANAGEMENT_FILE,
        test_management_manifest_response(test_properties),
    )
    _write_manifest_json(
        http_dir / TEST_OPTIMIZATION_SKIPPABLE_TESTS_FILE,
        skippable_manifest_response(skippable_items, itr_correlation_id),
    )
    _write_manifest_text(manifest_path, f"version = {SUPPORTED_MANIFEST_VERSION}\n")
    return manifest_path


def _write_manifest_text(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.replace(path)


def _write_manifest_json(path: Path, payload: dict[str, t.Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def settings_manifest_response(settings: Settings) -> dict[str, t.Any]:
    return {
        "data": {
            "attributes": {
                "early_flake_detection": {
                    "enabled": settings.early_flake_detection.enabled,
                    "slow_test_retries": {
                        "5s": settings.early_flake_detection.slow_test_retries_5s,
                        "10s": settings.early_flake_detection.slow_test_retries_10s,
                        "30s": settings.early_flake_detection.slow_test_retries_30s,
                        "5m": settings.early_flake_detection.slow_test_retries_5m,
                    },
                    "faulty_session_threshold": settings.early_flake_detection.faulty_session_threshold,
                },
                "flaky_test_retries_enabled": settings.auto_test_retries.enabled,
                "known_tests_enabled": settings.known_tests_enabled,
                "code_coverage": settings.coverage_enabled,
                "tests_skipping": settings.skipping_enabled,
                "require_git": settings.require_git,
                "itr_enabled": settings.itr_enabled,
                "coverage_report_upload_enabled": settings.coverage_report_upload_enabled,
                "test_management": {
                    "enabled": settings.test_management.enabled,
                    "attempt_to_fix_retries": settings.test_management.attempt_to_fix_retries,
                },
            }
        }
    }


def known_tests_manifest_response(known_tests: set[TestRef]) -> dict[str, t.Any]:
    tests: dict[str, dict[str, list[str]]] = {}
    for test_ref in known_tests:
        module_tests = tests.setdefault(test_ref.suite.module.name, {})
        suite_tests = module_tests.setdefault(test_ref.suite.name, [])
        suite_tests.append(test_ref.name)
    for suites in tests.values():
        for suite_tests in suites.values():
            suite_tests.sort()
    return {"data": {"attributes": {"tests": tests}}}


def test_management_manifest_response(test_properties: dict[TestRef, TestProperties]) -> dict[str, t.Any]:
    modules: dict[str, t.Any] = {}
    for test_ref, properties in test_properties.items():
        module_data = modules.setdefault(test_ref.suite.module.name, {"suites": {}})
        suite_data = module_data["suites"].setdefault(test_ref.suite.name, {"tests": {}})
        suite_data["tests"][test_ref.name] = {
            "properties": {
                "quarantined": properties.quarantined,
                "disabled": properties.disabled,
                "attempt_to_fix": properties.attempt_to_fix,
                "active": properties.active,
            }
        }
    return {"data": {"attributes": {"modules": modules}}}


def skippable_manifest_response(
    skippable_items: set[t.Union[SuiteRef, TestRef]], itr_correlation_id: t.Optional[str]
) -> dict[str, t.Any]:
    data = []
    for item in skippable_items:
        attributes = {
            "suite": item.suite.name if isinstance(item, TestRef) else item.name,
            "configurations": {
                "test.bundle": item.suite.module.name if isinstance(item, TestRef) else item.module.name
            },
        }
        if isinstance(item, TestRef):
            attributes["name"] = item.name
            item_type = "test"
        else:
            item_type = "suite"
        data.append({"type": item_type, "attributes": attributes})
    return {"data": data, "meta": {"correlation_id": itr_correlation_id}}
