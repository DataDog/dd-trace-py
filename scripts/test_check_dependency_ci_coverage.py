#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "packaging>=23.1,<24",
#     "requests>=2.28,<3",
#     "ruamel.yaml>=0.17.21",
#     "setuptools<82",
# ]
# ///
"""Lookup failures must not be reported as missing major coverage."""

from importlib.machinery import ModuleSpec
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
from unittest.mock import _patch
from unittest.mock import call
from unittest.mock import patch

from packaging.version import Version
import requests


_SCRIPT: Path = Path(__file__).with_name("check-dependency-ci-coverage.py")
_spec: ModuleSpec | None = importlib.util.spec_from_file_location("check_dependency_ci_coverage", _SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load {_SCRIPT}")
_coverage: types.ModuleType = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_coverage)

_LOOKUP_FAILURE: str = (
    "wrapt: PyPI lookup failed for 'wrapt' after 3 attempts "
    "(request failed, timed out, or returned no version). "
    "Cannot determine the latest major."
)


def _json_response(status_code: int, version: str | None) -> Mock:
    response: Mock = Mock()
    response.status_code = status_code
    body: dict[str, object]
    if version is None:
        body = {"info": {}}
    else:
        body = {"info": {"version": version}}
    response.json.return_value = body
    return response


def _wrapt_pyproject() -> dict[str, object]:
    location: object = _coverage.Location("pyproject.toml", 1)
    dep: object = _coverage.PyprojectDep(
        majors={1, 2},
        specifier="<3,>=1",
        location=location,
    )
    pyproject: dict[str, object] = {"wrapt": dep}
    return pyproject


def _wrapt_ci(latest_major: int | None) -> dict[str, object]:
    """Bare wrapt plus an explicit major-1 bound, matching the suitespec pair."""
    info: object = _coverage.DepInfo(
        majors={1},
        has_latest=True,
        latest_major=latest_major,
        locations=[_coverage.Location("tests/suitespec.yml", 328)],
    )
    tested: dict[str, object] = {"wrapt": info}
    return tested


class CheckDependencyCiCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        _coverage.get_pypi_latest_version.cache_clear()
        sleep_patch: _patch[Mock] = patch.object(_coverage.time, "sleep")
        self.sleep: Mock = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def test_empty_wrapt_lookup_is_not_missing_coverage(self) -> None:
        timeout: requests.exceptions.Timeout = requests.exceptions.Timeout("timed out")
        denied: Mock = _json_response(500, None)
        empty: Mock = _json_response(200, None)
        get: Mock
        with patch.object(_coverage.requests, "get", side_effect=[timeout, denied, empty]) as get:
            latest: Version | None = _coverage.get_pypi_latest_version("wrapt")

        self.assertEqual(get.call_count, _coverage._PYPI_LOOKUP_ATTEMPTS)
        self.assertEqual(self.sleep.call_args_list, [call(1.0), call(2.0)])
        self.assertIsNone(latest)

        result: tuple[list[str], list[str], list[object]] = _coverage.check_coverage(
            _wrapt_pyproject(),
            _wrapt_ci(None),
        )
        errors: list[str] = result[0]
        joined: str = "\n".join(errors)
        self.assertNotIn("Missing coverage for major(s): [2]", joined)
        self.assertIn(_LOOKUP_FAILURE, joined)

    def test_latest_major_the_matrix_does_not_test_still_fails_coverage(self) -> None:
        # PyPI answers: latest is 3.x, which the explicit matrix (major 1) does not test.
        # Declared majors are 1 and 2, so major 2 is still missing.
        found: Mock = _json_response(200, "3.0.0")
        get: Mock
        with patch.object(_coverage.requests, "get", return_value=found) as get:
            latest: Version | None = _coverage.get_pypi_latest_version("wrapt")

        self.assertEqual(get.call_count, 1)
        self.assertEqual(self.sleep.call_count, 0)
        self.assertIsNotNone(latest)
        resolved: Version = latest if latest is not None else Version("0")
        self.assertEqual(resolved.major, 3)

        result: tuple[list[str], list[str], list[object]] = _coverage.check_coverage(
            _wrapt_pyproject(),
            _wrapt_ci(resolved.major),
        )
        errors: list[str] = result[0]
        joined: str = "\n".join(errors)
        self.assertIn("Missing coverage for major(s): [2]", joined)
        self.assertNotIn("PyPI lookup failed", joined)

    def test_failed_lookup_is_quiet_when_explicit_bounds_already_cover(self) -> None:
        location: object = _coverage.Location("pyproject.toml", 1)
        dep: object = _coverage.PyprojectDep(majors={1}, specifier="<2,>=1", location=location)
        pyproject: dict[str, object] = {"wrapt": dep}
        info: object = _coverage.DepInfo(
            majors={1},
            has_latest=True,
            latest_major=None,
            locations=[_coverage.Location("tests/suitespec.yml", 331)],
        )
        tested: dict[str, object] = {"wrapt": info}
        timeout: requests.exceptions.Timeout = requests.exceptions.Timeout("timed out")
        with patch.object(_coverage.requests, "get", side_effect=timeout):
            result: tuple[list[str], list[str], list[object]] = _coverage.check_coverage(pyproject, tested)
        joined: str = "\n".join(result[0])
        self.assertNotIn("PyPI lookup failed", joined)
        self.assertNotIn("Missing coverage for major(s)", joined)


if __name__ == "__main__":
    unittest.main()
