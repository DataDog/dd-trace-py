import argparse
from collections import defaultdict
import datetime as dt
from http.client import HTTPSConnection
from io import StringIO
import json
from operator import itemgetter
import os
import pathlib
import sys
import typing
from typing import Optional

from packaging.version import Version
from pip import _internal

from ddtrace.contrib.integration_registry.mappings import (
    MODULE_TO_DEPENDENCY_MAPPING, INTEGRATION_TO_DEPENDENCY_MAPPING, DEPENDENCY_TO_INTEGRATION_MAPPING
)

sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
import riotfile  # noqa: E402


CONTRIB_ROOT = pathlib.Path("ddtrace/contrib/internal")
LATEST = ""

dependency_module_mapping = {v: k for k, v in MODULE_TO_DEPENDENCY_MAPPING.items()}

supported_versions = []
pinned_packages = set()

VENV_TO_SUBVENVS = {}
VENV_HASH_TO_NAME = {}


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
    usage: python scripts/freshvenvs.py <output> OR <generate>
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["output", "generate"], help="mode: output or generate")
    return parser.parse_args()

def _get_integrated_modules() -> typing.Set[str]:
    """Get all modules that have contribs implemented for them"""
    all_required_modules = set()
    for item in CONTRIB_ROOT.iterdir():
        if not os.path.isdir(item):
            continue

        patch_filepath = item / "patch.py"

        if os.path.isfile(patch_filepath):
            module_name = item.name
            all_required_modules.add(module_name)


    return all_required_modules


def _get_riot_envs_including_any(modules: typing.Set[str]) -> typing.Set[str]:
    """Return the set of riot env hashes where each env uses at least one of the given modules"""
    envs = set()
    for item in os.listdir(".riot/requirements"):
        if item.endswith(".txt"):
            with open(f".riot/requirements/{item}", "r") as lockfile:
                lockfile_content = lockfile.read()
                for module in modules:
                    if module in lockfile_content or (
                        (module in MODULE_TO_DEPENDENCY_MAPPING and MODULE_TO_DEPENDENCY_MAPPING[module] in lockfile_content)
                        or _integration_to_dependency_mapping_contains(module, lockfile_content)
                    ):
                        envs |= {item.split(".")[0]}
                        break
    return envs

def _integration_to_dependency_mapping_contains(module: str, lockfile_content: str) -> bool:
    if not module in INTEGRATION_TO_DEPENDENCY_MAPPING:
        return False
    
    for dependency in INTEGRATION_TO_DEPENDENCY_MAPPING[module]:
        if dependency in lockfile_content:
            return True

    return False

def _get_updatable_packages_implementing(modules: typing.Set[str]) -> typing.Set[str]:
    """Return all packages have contribs implemented for them"""
    all_venvs = riotfile.venv.venvs
    all_venvs = _propagate_venv_names_to_child_venvs(all_venvs)

    packages_setting_latest = set()
    def recurse_venvs(venvs: typing.List[riotfile.Venv]):
        for venv in venvs:
            package = venv.name
            if package not in modules:
                continue
            if not _venv_sets_latest_for_package(venv, package) and not package in packages_setting_latest:
                pinned_packages.add(package)
            else:
                packages_setting_latest.add(package)
                if package in pinned_packages:
                    pinned_packages.remove(package)
            recurse_venvs(venv.venvs)
    
    recurse_venvs(all_venvs)

    packages = {m for m in modules if "." not in m and m not in pinned_packages}
    return packages


def _propagate_venv_names_to_child_venvs(all_venvs: typing.List[riotfile.Venv]) -> typing.List[riotfile.Venv]:
    """Propagate the venv name to child venvs, since most child venvs in riotfile are unnamed. Since all contrib
    venvs are nested within eachother, we will get a consistent integration name for each venv / child venv"""
    for venv in all_venvs:
        if venv.venvs:
            for child_venv in venv.venvs:
                if child_venv.name:
                    VENV_TO_SUBVENVS[child_venv.name] = venv.name
                child_venv.name = venv.name

    return all_venvs


def _get_all_modules(modules: typing.Set[str]) -> typing.Set[str]:
    """Return all packages have contribs implemented for them"""
    contrib_modules = {m for m in modules if "." not in m}
    return contrib_modules


def _get_version_extremes(package_name: str) -> typing.Tuple[Optional[str], Optional[str]]:
    """Return the (earliest, latest) supported versions of a given package"""
    with Capturing() as output:
        _internal.main(["index", "versions", package_name])
    if not output:
        return (None, None)
    version_list = [a for a in output if "available versions" in a.lower()][0]
    output_parts = version_list.split()
    versions = [p.strip(",") for p in output_parts[2:]]
    earliest_within_window = versions[-1]

    conn = HTTPSConnection("pypi.org", 443)
    conn.request("GET", f"pypi/{package_name}/json")
    response = conn.getresponse()

    if response.status != 200:
        raise ValueError(f"Failed to connect to PyPI: HTTP {response.status}")

    version_infos = json.loads(response.readlines()[0])["releases"]
    for version in versions:
        version_info = version_infos.get(version, [])
        if not version_info:
            continue

        upload_timestamp = version_info[0].get("upload_time_iso_8601")
        upload_time = dt.datetime.strptime(upload_timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        upload_time = upload_time.replace(tzinfo=dt.timezone.utc)
        current_time = dt.datetime.now(dt.timezone.utc)
        version_age = current_time - upload_time
        if version_age > dt.timedelta(days=365 * 2):
            earliest_within_window = version
            break
    return earliest_within_window, versions[0]


def _get_riot_hash_to_venv_name() -> typing.Dict[str, str]:
    """Get a mapping of riot hash to venv name."""
    import riot
    import re
    from io import StringIO
    
    ctx = riot.Session.from_config_file("riotfile.py")
    old_stdout = sys.stdout
    result = StringIO()
    sys.stdout = result
    
    try:
        pattern = re.compile(r"^.*$")
        venv_pattern = re.compile(r"^.*$")
        ctx.list_venvs(pattern=pattern, venv_pattern=venv_pattern, pipe_mode=True)
        output = result.getvalue()
    finally:
        sys.stdout = old_stdout
    
    hash_to_name = {}
    for line in output.splitlines():
        match = re.match(r'\[#\d+\]\s+([a-f0-9]+)\s+(\S+)', line)
        if match:
            venv_hash, venv_name = match.groups()
            hash_to_name[venv_hash] = venv_name
    return hash_to_name


def _get_package_versions_from(env: str, packages: typing.Set[str], riot_hash_to_venv_name: typing.Dict[str, str]) -> typing.List[typing.Tuple[str, str]]:
    """Return the list of package versions that are tested, related to the modules"""
    lockfile_content = pathlib.Path(f".riot/requirements/{env}.txt").read_text().splitlines()
    lock_packages = []
    integration = None
    dependencies = []
    if riot_hash_to_venv_name.get(env):
        venv_name = riot_hash_to_venv_name[env].split("[")[0]

        def get_integration_and_dependencies(venv_name: str) -> typing.Tuple[str, typing.List[str]]:
            if venv_name in packages:
                integration = venv_name
                dependencies = INTEGRATION_TO_DEPENDENCY_MAPPING.get(venv_name, integration)
                return integration, dependencies
            elif venv_name in DEPENDENCY_TO_INTEGRATION_MAPPING:
                integration = DEPENDENCY_TO_INTEGRATION_MAPPING[venv_name]
                dependencies = INTEGRATION_TO_DEPENDENCY_MAPPING[integration]
                return integration, dependencies
            elif venv_name in VENV_TO_SUBVENVS:
                main_venv = VENV_TO_SUBVENVS[venv_name]
                return get_integration_and_dependencies(main_venv)
            else:
                return None, []
        integration, dependencies = get_integration_and_dependencies(venv_name)


    for line in lockfile_content:
        package, _, versions = line.partition("==")
        package = package.split("[")[0] # strip optional package installs
        if package in packages:
            lock_packages.append((package, versions))
        if package in dependencies or package == integration:
            lock_packages.append((package, versions))
        elif package in dependency_module_mapping and dependency_module_mapping[package] in packages:
            lock_packages.append((dependency_module_mapping[package], versions))
    return lock_packages


def _is_module_autoinstrumented(module: str) -> bool:
    import importlib

    _monkey = importlib.import_module("ddtrace._monkey")
    PATCH_MODULES = getattr(_monkey, "PATCH_MODULES")

    return module in PATCH_MODULES and PATCH_MODULES[module]

def _versions_fully_cover_bounds(bounds: typing.Tuple[str, str], versions: typing.List[str]) -> bool:
    """Return whether the tested versions cover the upper bound range of supported versions"""
    if not versions:
        return False
    _, upper_bound = bounds
    return versions[0] >= Version(upper_bound)


def _venv_sets_latest_for_package(venv: riotfile.Venv, suite_name: str) -> bool:
    """
    Returns whether the Venv for the package uses `latest` or not.
    DFS traverse through the Venv, as it may have nested Venvs.

    If the module name is in MODULE_TO_DEPENDENCY_MAPPING, remap it.
    """
    package = MODULE_TO_DEPENDENCY_MAPPING.get(suite_name, suite_name)

    if package in venv.pkgs:
        if LATEST in venv.pkgs[package]:
            return True

    if venv.venvs:
        for child_venv in venv.venvs:
            if _venv_sets_latest_for_package(child_venv, package):
                return True

    return False


def _get_all_used_versions(envs, packages, riot_hash_to_venv_name) -> dict:
    """
    Returns dict(module, set(versions)) for a venv, as defined from riot lockfiles.
    """
    all_used_versions = defaultdict(set)
    for env in envs:
        versions_used = _get_package_versions_from(env, packages, riot_hash_to_venv_name)
        for package, version in versions_used:
            all_used_versions[package].add(version)
    return all_used_versions


def _get_version_bounds(packages) -> dict:
    """
    Return dict(module: (earliest, latest)) of the module from PyPI
    """
    bounds = dict()
    for package in packages:
        earliest, latest = _get_version_extremes(package)
        bounds[package] = (earliest, latest)
    return bounds

def output_outdated_packages(all_required_packages, envs, bounds, riot_hash_to_venv_name):
    """
    Output a list of package names that can be updated.
    """
    outdated_packages = []

    for package in all_required_packages:
        earliest, latest = _get_version_extremes(package)
        bounds[package] = (earliest, latest)

    all_used_versions = defaultdict(set)
    for env in envs:
        versions_used = _get_package_versions_from(env, all_required_packages, riot_hash_to_venv_name)
        for pkg, version in versions_used:
            all_used_versions[pkg].add(version)


    for package in all_required_packages:
        ordered = sorted([Version(v) for v in all_used_versions[package]], reverse=True)
        if not ordered:
            continue
        if package not in bounds or bounds[package] == (None, None):
            continue
        if not _versions_fully_cover_bounds(bounds[package], ordered):
            outdated_packages.append(package)

    print(" ".join(outdated_packages))


def generate_supported_versions(contrib_packages, all_used_versions):
    """
    Generate supported versions JSON
    """
    patched = {}
    for package in contrib_packages:
        for dependency in INTEGRATION_TO_DEPENDENCY_MAPPING.get(package, [package]):
            ordered = sorted([Version(v) for v in all_used_versions[dependency]], reverse=True)
            if not ordered:
                continue
            json_format = {
                "dependency": dependency or package,
                "integration": package,
                "minimum_tracer_supported": str(ordered[-1]),
                "max_tracer_supported": str(ordered[0]),
            }

            if package in pinned_packages:
                json_format["pinned"] = "true"

            if package not in patched:
                patched[package] = _is_module_autoinstrumented(package)
            json_format["auto-instrumented"] = patched[package]
            supported_versions.append(json_format)

    supported_versions_output = sorted(supported_versions, key=itemgetter("integration"))
    with open("supported_versions_output.json", "w") as file:
        json.dump(supported_versions_output, file, indent=4)

def main():
    args = parse_args()
    all_required_modules = _get_integrated_modules()
    all_required_packages = _get_updatable_packages_implementing(all_required_modules)  # MODULE names
    riot_hash_to_venv_name = _get_riot_hash_to_venv_name()
    envs = _get_riot_envs_including_any(all_required_modules)
    contrib_packages = _get_all_modules(all_required_modules)

    if args.mode == "output":
        bounds = _get_version_bounds(contrib_packages)
        output_outdated_packages(all_required_packages, envs, bounds)
    elif args.mode == "generate":
        all_used_versions = _get_all_used_versions(envs, contrib_packages, riot_hash_to_venv_name)
        generate_supported_versions(contrib_packages, all_used_versions)


if __name__ == "__main__":
    main()
