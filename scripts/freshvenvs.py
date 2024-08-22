import ast
from collections import defaultdict
import datetime as dt
from http.client import HTTPSConnection
from io import StringIO
import json
import os
import pathlib
import sys
import typing

from packaging.version import Version
from pip import _internal


sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))

CONTRIB_ROOT = pathlib.Path("ddtrace/contrib")


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
        init_filepath = item / "__init__.py"
        if os.path.isdir(item) and os.path.isfile(init_filepath):
            with open(init_filepath, "r") as initfile:
                initfile_content = initfile.read()
            syntax_tree = ast.parse(initfile_content)
            for node in syntax_tree.body:
                if hasattr(node, "targets"):
                    if node.targets[0].id == "required_modules":
                        to_add = set()
                        for mod in node.value.elts:
                            to_add |= {mod.value, mod.value.split(".")[0]}
                        all_required_modules |= to_add
    return all_required_modules


def _get_riot_envs_including_any(modules: typing.Set[str]) -> typing.Set[str]:
    """Return the set of riot env hashes where each env uses at least one of the given modules"""
    envs = set()
    for item in os.listdir(".riot/requirements"):
        if item.endswith(".txt"):
            with open(f".riot/requirements/{item}", "r") as lockfile:
                lockfile_content = lockfile.read()
                for module in modules:
                    if module in lockfile_content:
                        envs |= {item.split(".")[0]}
                        break
    return envs


def _get_packages_implementing(modules: typing.Set[str]) -> typing.Set[str]:
    return {m for m in modules if "." not in m}


def _get_version_extremes(package_name: str) -> typing.Tuple[str, str]:
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
    """Return the list of package versions that are tested"""
    lockfile_content = pathlib.Path(f".riot/requirements/{env}.txt").read_text().splitlines()
    lock_packages = []
    for line in lockfile_content:
        package, _, versions = line.partition("==")
        if package in packages:
            lock_packages.append((package, versions))
    return lock_packages


def _versions_fully_cover_bounds(bounds: typing.Tuple[str, str], versions: typing.List[str]) -> bool:
    """Return whether the tested versions cover the full range of supported versions"""
    if not versions:
        return False
    return versions[0] >= Version(bounds[1]) and versions[-1] <= Version(bounds[0])


def main():
    all_required_modules = _get_integrated_modules()
    all_required_packages = _get_packages_implementing(all_required_modules)
    envs = _get_riot_envs_including_any(all_required_modules)

    bounds = dict()
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
            print(
                f"{package}: policy supports version {bounds[package][0]} through {bounds[package][1]} "
                f"but only these versions are used: {[str(v) for v in ordered]}"
            )


if __name__ == "__main__":
    main()
