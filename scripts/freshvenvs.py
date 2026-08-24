#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "packaging>=23.1,<26",
#     "pip>=25,<26",
#     "pyyaml>=6,<7",
#     "ruamel.yaml>=0.17.21",
# ]
# ///

import argparse
from collections import defaultdict
import datetime as dt
from functools import lru_cache
from http.client import HTTPSConnection
from io import StringIO
import json
import pathlib
import sys
from typing import Optional

from packaging.requirements import Requirement
from packaging.version import Version
from pip import _internal


# Add project root and integration-registry helpers to the import path.
sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
sys.path.append(str(pathlib.Path(__file__).parent.resolve() / "integration_registry"))

from mappings import DEPENDENCY_TO_INTEGRATION_MAPPING  # noqa: I001,E402
from mappings import INTEGRATION_TO_DEPENDENCY_MAPPING  # noqa: I001,E402

from tests.suitespec import TestEnvironment  # noqa: I001,E402
from tests.suitespec import get_test_environments  # noqa: I001,E402
from scripts._testenv import COOLDOWN_DAYS  # noqa: I001,E402

CONTRIB_ROOT = pathlib.Path("ddtrace/contrib/internal")


class Capturing(list):
    def __enter__(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = self._stringio = StringIO()
        sys.stderr = StringIO()
        return self

    def __exit__(self, *args):
        self.extend(self._stringio.getvalue().splitlines())
        del self._stringio  # free up some memory
        sys.stdout = self._stdout
        sys.stderr = self._stderr


def parse_args():
    """
    usage: scripts/freshvenvs.py <output>
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["output"], help="mode: output")
    return parser.parse_args()


def _get_contrib_modules() -> set[str]:
    """Get all integrations by checking modules that have contribs implemented for them"""
    all_integration_names = set()
    for item in CONTRIB_ROOT.iterdir():
        if not item.is_dir():
            continue

        patch_filepath = item / "patch.py"

        if patch_filepath.is_file():
            all_integration_names.add(item.name)

    return all_integration_names


def _all_test_environments() -> tuple[TestEnvironment, ...]:
    return tuple(
        environment for environments in get_test_environments(nightly=False).values() for environment in environments
    )


def _get_test_environments_including_any(contrib_modules: set[str]) -> tuple[TestEnvironment, ...]:
    return tuple(
        environment for environment in _all_test_environments() if environment.integration_name in contrib_modules
    )


def _get_updatable_packages_implementing(contrib_modules: set[str]) -> set[str]:
    """Return integrations with an environment that tracks their latest dependency."""
    packages_setting_latest = set()
    for environment in _all_test_environments():
        integration = environment.integration_name
        if integration not in contrib_modules:
            continue
        dependencies = {
            dependency.lower() for dependency in INTEGRATION_TO_DEPENDENCY_MAPPING.get(integration, {integration})
        }
        for value in environment.direct_dependencies:
            requirement = Requirement(value)
            if requirement.name.lower() in dependencies and not requirement.specifier:
                packages_setting_latest.add(integration)
                break
    return {package for package in packages_setting_latest if "." not in package}


def _parse_pypi_upload_time(upload_timestamp: str) -> Optional[dt.datetime]:
    """Best-effort parse of a PyPI ``upload_time_iso_8601`` timestamp.

    PyPI usually returns ``YYYY-MM-DDTHH:MM:SS.fffZ`` but some old releases
    omit the microseconds component, so we try both formats and return
    ``None`` if neither matches.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(upload_timestamp, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


@lru_cache(maxsize=256)
def _get_version_extremes(contrib_module: str) -> tuple[Optional[str], Optional[str]]:
    """Return the (earliest, latest) supported versions of a given package.

    The returned ``latest`` is the most recent PyPI release that is at least
    ``COOLDOWN_DAYS`` old. Versions younger than that are ignored so the
    automated lockfile-refresh workflow does not pull in a release before
    the supply-chain "cooldown" period has elapsed (see TEST-CD).
    """
    with Capturing() as output:
        _internal.main(["index", "versions", contrib_module])
    if not output:
        return (None, None)

    version_list = [a for a in output if "available versions" in a.lower()]
    if not version_list:
        return (None, None)

    output_parts = version_list[0].split()
    versions = [p.strip(",") for p in output_parts[2:]]
    if not versions:
        return (None, None)

    earliest_within_window = versions[-1]

    conn = None
    try:
        conn = HTTPSConnection("pypi.org", 443, timeout=30)
        conn.request("GET", f"/pypi/{contrib_module}/json")
        response = conn.getresponse()

        if response.status != 200:
            raise ValueError(f"Failed to connect to PyPI: HTTP {response.status}")

        version_infos = json.loads(response.read().decode("utf-8"))["releases"]
    except (OSError, json.JSONDecodeError, KeyError) as e:
        raise ValueError(f"Failed to fetch version info for {contrib_module}: {e}")
    finally:
        if conn is not None:
            conn.close()

    current_time = dt.datetime.now(dt.timezone.utc)
    cooldown = dt.timedelta(days=COOLDOWN_DAYS)
    two_years = dt.timedelta(days=365 * 2)

    # The first version in ``versions`` that is at least COOLDOWN_DAYS old.
    # Falls back to the absolute latest if we can't determine the age (for
    # example, PyPI's JSON API didn't return release files for any of the
    # candidates), since that preserves the prior behaviour rather than
    # silently disabling the outdated-package detection.
    latest_after_cooldown: Optional[str] = None

    for version in versions:
        version_info = version_infos.get(version, [])
        if not version_info:
            continue

        upload_timestamp = version_info[0].get("upload_time_iso_8601")
        if not upload_timestamp:
            continue

        upload_time = _parse_pypi_upload_time(upload_timestamp)
        if upload_time is None:
            continue

        version_age = current_time - upload_time

        if latest_after_cooldown is None and version_age >= cooldown:
            latest_after_cooldown = version

        if version_age > two_years:
            earliest_within_window = version
            break

    if latest_after_cooldown is None:
        latest_after_cooldown = versions[0]
    return earliest_within_window, latest_after_cooldown


def _get_package_versions_from(environment: TestEnvironment, contrib_modules: set[str]) -> list[tuple[str, str]]:
    """Return the list of package versions that are tested, related to the modules"""
    lockfile_content = environment.lockfile.read_text().splitlines()
    lock_packages = []
    integration = environment.integration_name
    if integration not in contrib_modules and integration in DEPENDENCY_TO_INTEGRATION_MAPPING:
        integration = DEPENDENCY_TO_INTEGRATION_MAPPING[integration]
    dependencies = INTEGRATION_TO_DEPENDENCY_MAPPING.get(integration) or {integration}

    for line in lockfile_content:
        package, _, versions = line.partition("==")
        package = package.split("[")[0]  # strip optional package installs like flask[async]
        if package in dependencies or package == integration:
            lock_packages.append((package, versions))
    return lock_packages


def _is_module_autoinstrumented(module: str) -> bool:
    import importlib

    _monkey = importlib.import_module("ddtrace._monkey")
    PATCH_MODULES = getattr(_monkey, "PATCH_MODULES")

    return module in PATCH_MODULES and PATCH_MODULES[module]


def _versions_fully_cover_bounds(bounds: tuple[str, str], versions: list[Version]) -> bool:
    """Return whether the tested versions cover the upper bound range of supported versions"""
    if not versions:
        return False
    _, upper_bound = bounds
    return versions[0] >= Version(upper_bound)


def _get_version_bounds(contrib_modules: set[str]) -> dict:
    """
    Return dict(module: (earliest, latest)) of the module from PyPI
    """
    bounds = dict()
    for contrib_module in contrib_modules:
        earliest, latest = _get_version_extremes(contrib_module)
        bounds[contrib_module] = (earliest, latest)
    return bounds


def output_outdated_packages(all_updatable_contribs, environments, bounds):
    """
    Output a list of package names that can be updated.
    """
    outdated_packages = []

    for contrib_module in all_updatable_contribs:
        earliest, latest = _get_version_extremes(contrib_module)
        bounds[contrib_module] = (earliest, latest)

    all_used_versions = defaultdict(set)
    for environment in environments:
        versions_used = _get_package_versions_from(environment, all_updatable_contribs)
        for pkg, version in versions_used:
            all_used_versions[pkg].add(version)

    for contrib_module in all_updatable_contribs:
        ordered = sorted([Version(v) for v in all_used_versions[contrib_module]], reverse=True)
        if not ordered:
            continue
        if contrib_module not in bounds or bounds[contrib_module] == (None, None):
            continue
        if not _versions_fully_cover_bounds(bounds[contrib_module], ordered):
            outdated_packages.append(contrib_module)

    print(" ".join(outdated_packages))


def main():
    parse_args()
    contribs = _get_contrib_modules()
    all_updatable_contribs = _get_updatable_packages_implementing(contribs)  # MODULE names
    environments = _get_test_environments_including_any(contribs)

    bounds = _get_version_bounds(contribs)
    output_outdated_packages(all_updatable_contribs, environments, bounds)


if __name__ == "__main__":
    main()
