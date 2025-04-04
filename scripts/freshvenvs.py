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


sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
import riotfile  # noqa: E402


CONTRIB_ROOT = pathlib.Path("ddtrace/contrib/internal")
LATEST = ""

excluded = {"coverage"}

# map module => lockfile dependency
module_dependency_mapping = {
    "kafka": "confluent-kafka",
    "consul": "python-consul",
    "snowflake": "snowflake-connector-python",
    "flask_cache": "flask-caching",
    "graphql": "graphql-core",
    "mysql": "mysql-connector-python",
    "mysqldb": "mysqlclient",
    "asyncio": "pytest-asyncio",
    "sqlite3": "pysqlite3-binary",
    "grpc": "grpcio",
    "google_generativeai": "google-generativeai",
    "psycopg2": "psycopg2-binary",
    "cassandra": "cassandra-driver",
    "rediscluster": "redis-py-cluster",
    "dogpile_cache": "dogpile-cache",
    "vertica": "vertica-python",
    "aiohttp_jinja2": "aiohttp-jinja2",
    "azure_functions": "azure-functions",
    "pytest_bdd": "pytest-bdd",
}

# map lockfile dependency => module name
dependency_module_mapping = {v: k for k, v in module_dependency_mapping.items()}

supported_versions = []  # list of dicts
pinned_packages = set()


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
                        module in module_dependency_mapping and module_dependency_mapping[module] in lockfile_content
                    ):
                        envs |= {item.split(".")[0]}
                        break
    return envs


def _get_updatable_packages_implementing(modules: typing.Set[str]) -> typing.Set[str]:
    """Return all packages have contribs implemented for them"""
    all_venvs = riotfile.venv.venvs

    for v in all_venvs:
        package = v.name
        if package not in modules:
            continue
        if not _venv_sets_latest_for_package(v, package):
            pinned_packages.add(package)

    packages = {m for m in modules if "." not in m and m not in pinned_packages}
    return packages


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


def _get_package_versions_from(env: str, packages: typing.Set[str]) -> typing.List[typing.Tuple[str, str]]:
    """Return the list of package versions that are tested, related to the modules"""
    # Returns [(package, version), (package, versions)]
    lockfile_content = pathlib.Path(f".riot/requirements/{env}.txt").read_text().splitlines()
    lock_packages = []
    for line in lockfile_content:
        package, _, versions = line.partition("==")
        if package in packages:
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
    """Return whether the tested versions cover the full range of supported versions"""
    if not versions:
        return False
    _, upper_bound = bounds
    return versions[0] >= Version(upper_bound)


def _venv_sets_latest_for_package(venv: riotfile.Venv, suite_name: str) -> bool:
    """
    Returns whether the Venv for the package uses `latest` or not.
    DFS traverse through the Venv, as it may have nested Venvs.

    If the module name is in module_dependency_mapping, remap it.
    """
    package = module_dependency_mapping.get(suite_name, suite_name)

    if package in venv.pkgs:
        if LATEST in venv.pkgs[package]:
            return True

    if venv.venvs:
        for child_venv in venv.venvs:
            if _venv_sets_latest_for_package(child_venv, package):
                return True

    return False


def _get_all_used_versions(envs, packages) -> dict:
    # Returns dict(module, set(versions)) for a venv, as defined in riotfiles.
    all_used_versions = defaultdict(set)
    for env in envs:
        versions_used = _get_package_versions_from(env, packages)  # returns list of (package, versions)
        for package, version in versions_used:
            all_used_versions[package].add(version)
    return all_used_versions


def _get_version_bounds(packages) -> dict:
    # Return dict(module: (earliest, latest)) of the module on PyPI
    bounds = dict()
    for package in packages:
        earliest, latest = _get_version_extremes(package)
        bounds[package] = (earliest, latest)
    return bounds

def output_outdated_packages(all_required_packages, envs, bounds):
    outdated_packages = []

    for package in all_required_packages:
        earliest, latest = _get_version_extremes(package)
        bounds[package] = (earliest, latest)

    all_used_versions = defaultdict(set)
    for env in envs:
        versions_used = _get_package_versions_from(env, all_required_packages)
        for pkg, version in versions_used:
            all_used_versions[pkg].add(version)


    for package in all_required_packages:
        ordered = sorted([Version(v) for v in all_used_versions[package]], reverse=True)
        if not ordered:
            continue
        if not _versions_fully_cover_bounds(bounds[package], ordered):
            outdated_packages.append(package)

    print(" ".join(outdated_packages))


def generate_supported_versions(contrib_packages, all_used_versions, patched):
    # Generate supported versions
    for package in contrib_packages:
        ordered = sorted([Version(v) for v in all_used_versions[package]], reverse=True)
        if not ordered:
            continue
        json_format = {
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
    all_required_modules = _get_integrated_modules()
    all_required_packages = _get_updatable_packages_implementing(all_required_modules)  # these are MODULE names
    contrib_modules = _get_all_modules(all_required_modules)
    envs = _get_riot_envs_including_any(all_required_modules)
    patched = {}
    contrib_packages = contrib_modules
    all_used_versions = _get_all_used_versions(envs, contrib_packages)
    bounds = _get_version_bounds(contrib_packages)

    if len(sys.argv) != 2:
        print("usage: python scripts/freshvenvs.py <output> or <generate>")
        return
    if sys.argv[1] == "output":
        output_outdated_packages(all_required_packages, envs, bounds)
    if sys.argv[1] == "generate":
        generate_supported_versions(contrib_packages, all_used_versions, patched)


if __name__ == "__main__":
    main()
