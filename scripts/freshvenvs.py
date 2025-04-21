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
    INTEGRATION_TO_DEPENDENCY_MAPPING,
    DEPENDENCY_TO_INTEGRATION_MAPPING
)

sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
import riotfile  # noqa: E402


CONTRIB_ROOT = pathlib.Path("ddtrace/contrib/internal")
LATEST = ""

supported_versions = []
pinned_packages = set()

VENV_TO_SUBVENVS = {}


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


def _get_contrib_modules() -> typing.Set[str]:
    """Get all integrations by checking modules that have contribs implemented for them"""
    all_integration_names = set()
    for item in CONTRIB_ROOT.iterdir():
        if not os.path.isdir(item):
            continue

        patch_filepath = item / "patch.py"

        if os.path.isfile(patch_filepath):
            module_name = item.name
            all_integration_names.add(module_name)

    return all_integration_names


def _get_riot_envs_including_any(contrib_modules: typing.Set[str]) -> typing.Set[str]:
    """Return the set of riot env hashes where each env uses at least one of the given modules"""
    envs = set()
    for item in os.listdir(".riot/requirements"):
        if item.endswith(".txt"):
            with open(f".riot/requirements/{item}", "r") as lockfile:
                lockfile_content = lockfile.read()
                for contrib_module in contrib_modules:
                    if contrib_module in lockfile_content or (
                        _integration_to_dependency_mapping_contains(contrib_module, lockfile_content)
                    ):
                        envs |= {item.split(".")[0]}
                        break
    return envs


def _integration_to_dependency_mapping_contains(integration: str, lockfile_content: str) -> bool:
    if not integration in INTEGRATION_TO_DEPENDENCY_MAPPING:
        return False
    
    for dependency in INTEGRATION_TO_DEPENDENCY_MAPPING[integration]:
        if dependency in lockfile_content:
            return True

    return False


def _get_updatable_packages_implementing(contrib_modules: typing.Set[str]) -> typing.Set[str]:
    """Return all integrations that can be updated"""
    all_venvs = riotfile.venv.venvs
    all_venvs = _propagate_venv_names_to_child_venvs(all_venvs)

    packages_setting_latest = set()
    def recurse_venvs(venvs: typing.List[riotfile.Venv]):
        for venv in venvs:
            package = venv.name.split(":")[0] if venv.name is not None else venv.name # strip optional package installs
            # Check if the package name is an integration as all contrib venvs are named after the integration
            if package not in contrib_modules:
                continue
            if not _venv_sets_latest_for_package(venv, package) and not package in packages_setting_latest:
                pinned_packages.add(package)
            else:
                packages_setting_latest.add(package)
                if package in pinned_packages:
                    pinned_packages.remove(package)
            recurse_venvs(venv.venvs)
    
    recurse_venvs(all_venvs)

    packages = {m for m in contrib_modules if "." not in m and m not in pinned_packages}
    return packages


def _propagate_venv_names_to_child_venvs(all_venvs: typing.List[riotfile.Venv]) -> typing.List[riotfile.Venv]:
    """Propagate the venv name to child venvs, since most child venvs in riotfile are unnamed. Since most contrib
    venvs are nested within eachother, we will get a consistent integration name for each venv / child venv. Also
    lowercase the package names to ensure consistent lookups."""
    def _lower_pkg_names(venv: riotfile.Venv):
        venv.pkgs = {k.lower(): v for k, v in venv.pkgs.items()}
    
    for venv in all_venvs:
        _lower_pkg_names(venv)
        if venv.venvs:
            for child_venv in venv.venvs:
                if child_venv.name:
                    VENV_TO_SUBVENVS[child_venv.name] = venv.name
                child_venv.name = venv.name

    return all_venvs


def _get_version_extremes(contrib_module: str) -> typing.Tuple[Optional[str], Optional[str]]:
    """Return the (earliest, latest) supported versions of a given package"""
    with Capturing() as output:
        _internal.main(["index", "versions", contrib_module])
    if not output:
        return (None, None)
    version_list = [a for a in output if "available versions" in a.lower()][0]
    output_parts = version_list.split()
    versions = [p.strip(",") for p in output_parts[2:]]
    earliest_within_window = versions[-1]

    conn = HTTPSConnection("pypi.org", 443)
    conn.request("GET", f"pypi/{contrib_module}/json")
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
            hash_to_name[venv_hash] = venv_name.lower()
    return hash_to_name


def _get_package_versions_from(env: str, contrib_modules: typing.Set[str], riot_hash_to_venv_name: typing.Dict[str, str]) -> typing.List[typing.Tuple[str, str]]:
    """Return the list of package versions that are tested, related to the modules"""
    lockfile_content = pathlib.Path(f".riot/requirements/{env}.txt").read_text().splitlines()
    lock_packages = []
    integration = None
    dependencies = []
    if riot_hash_to_venv_name.get(env):
        venv_name = riot_hash_to_venv_name[env].split(":")[0]

        def get_integration_and_dependencies(venv_name: str) -> typing.Tuple[str, typing.List[str]]:
            if venv_name in contrib_modules:
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
        package = package.split("[")[0] # strip optional package installs like flask[async]
        if package in dependencies or package == integration:
            lock_packages.append((package, versions))
        elif package in DEPENDENCY_TO_INTEGRATION_MAPPING and DEPENDENCY_TO_INTEGRATION_MAPPING[package] == integration:
            lock_packages.append((DEPENDENCY_TO_INTEGRATION_MAPPING[package], versions))
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

    If the module name is in INTEGRATION_TO_DEPENDENCY_MAPPING, remap it.
    """
    packages = INTEGRATION_TO_DEPENDENCY_MAPPING.get(suite_name, [suite_name])

    for package in packages:
        if package in venv.pkgs:
            if LATEST in venv.pkgs[package]:
                return True

        if venv.venvs:
            for child_venv in venv.venvs:
                if _venv_sets_latest_for_package(child_venv, package):
                    return True
    return False


def _get_all_used_versions(envs, contrib_modules, riot_hash_to_venv_name) -> dict:
    """
    Returns dict(module, set(versions)) for a venv, as defined from riot lockfiles.
    """
    all_used_versions = defaultdict(set)
    for env in envs:
        versions_used = _get_package_versions_from(env, contrib_modules, riot_hash_to_venv_name)
        for package, version in versions_used:
            all_used_versions[package].add(version)
    return all_used_versions


def _get_version_bounds(contrib_modules: typing.Set[str]) -> dict:
    """
    Return dict(module: (earliest, latest)) of the module from PyPI
    """
    bounds = dict()
    for contrib_module in contrib_modules:
        earliest, latest = _get_version_extremes(contrib_module)
        bounds[contrib_module] = (earliest, latest)
    return bounds


def output_outdated_packages(all_updatable_contribs, envs, bounds, riot_hash_to_venv_name):
    """
    Output a list of package names that can be updated.
    """
    outdated_packages = []

    for contrib_module in all_updatable_contribs:
        earliest, latest = _get_version_extremes(contrib_module)
        bounds[contrib_module] = (earliest, latest)

    all_used_versions = defaultdict(set)
    for env in envs:
        versions_used = _get_package_versions_from(env, all_updatable_contribs, riot_hash_to_venv_name)
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


def generate_supported_versions(contrib_modules, all_used_versions):
    """
    Generate supported versions JSON
    """
    patched = {}
    for contrib_module in contrib_modules:
        for dependency in sorted(INTEGRATION_TO_DEPENDENCY_MAPPING.get(contrib_module, [contrib_module])):
            if dependency not in all_used_versions:
                # special case, some dependencies such as dogpile_cache can be installed
                # e.g. dogpile.cache or dogpile-cache or dogpile_cache, just use the one with versions
                for dep in INTEGRATION_TO_DEPENDENCY_MAPPING.get(contrib_module, [contrib_module]):
                    if dep in all_used_versions:
                        versions = all_used_versions[dep]
                        break
            else:
                versions = all_used_versions[dependency]
            ordered = sorted([Version(v) for v in versions], reverse=True)
            if not ordered:
                continue
            json_format = {
                "dependency": dependency or contrib_module,
                "integration": contrib_module,
                "minimum_tracer_supported": str(ordered[-1]),
                "max_tracer_supported": str(ordered[0]),
            }

            if contrib_module in pinned_packages:
                json_format["pinned"] = "true"

            if contrib_module not in patched:
                patched[contrib_module] = _is_module_autoinstrumented(contrib_module)
            json_format["auto-instrumented"] = patched[contrib_module]
            supported_versions.append(json_format)
            versions = []

    supported_versions_output = sorted(supported_versions, key=itemgetter("integration"))
    with open("supported_versions_output.json", "w") as file:
        json.dump(supported_versions_output, file, indent=4)


def main():
    args = parse_args()
    contribs = _get_contrib_modules()
    all_updatable_contribs = _get_updatable_packages_implementing(contribs)  # MODULE names
    riot_hash_to_venv_name = _get_riot_hash_to_venv_name()
    envs = _get_riot_envs_including_any(contribs)

    if args.mode == "output":
        bounds = _get_version_bounds(contribs)
        output_outdated_packages(all_updatable_contribs, envs, bounds, riot_hash_to_venv_name)
    elif args.mode == "generate":
        all_used_versions = _get_all_used_versions(envs, contribs, riot_hash_to_venv_name)
        generate_supported_versions(contribs, all_used_versions)


if __name__ == "__main__":
    main()
