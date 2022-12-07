import argparse
from datetime import datetime
import json
import sys
from urllib.error import HTTPError
from urllib.request import urlopen


ETERNITY = 0x7FFFFFFFFFFFFFFF

try:
    sys.path.extend([".", ".circleci"])
    from dependencies import LATEST_VERSIONS
except ModuleNotFoundError:
    print("usage (from the root directory of the project):\npython scripts/manage_dependencies.py", file=sys.stderr)
    sys.exit(-1)


def latest_version(packages: list[str]) -> dict[str, tuple[str, int, int]]:
    """
    Connect to pypi.org and retrieve information about the last available version
    and time elapsed since the current and last release
    """

    def get(package: str) -> tuple[str, int, int]:
        """return
        - the last version of a package
        - days since release of the current fixed version
        - days since releast of the last available version
        """
        try:
            res = urlopen(f"https://pypi.org/pypi/{package}/json")
            j = json.loads(res.read().decode())
            # j["info"]["version"] is officially the last published (larger) version of the package
            # index 0 is to get the first published wheel in this particular version
            d = datetime.now() - datetime.strptime(
                j["releases"][j["info"]["version"]][0]["upload_time"], "%Y-%m-%dT%H:%M:%S"
            )
            c = datetime.now() - datetime.strptime(
                j["releases"][LATEST_VERSIONS[package]][0]["upload_time"], "%Y-%m-%dT%H:%M:%S"
            )
            return j["info"]["version"], c.days, d.days
        except HTTPError:
            print(f"error on {package}")
            raise

    res = {}
    for p in packages:
        v = get(p)
        if v:
            res[p] = v
    return res


def read_versions(time: int) -> dict[str, tuple[str, int]]:
    """
    Show the latest version of packages that are not yet into LATEST_VERSIONS.
    May be used to understand why a test from the "latest" workflow is failing.
    Also show the packages with no update for more time days (in blue)
    return a dict, keys are package names, values are tuple of (version, days since this update)
    """
    new_versions = {}
    for package in LATEST_VERSIONS:
        lv, cdays, udays = latest_version([package])[package]
        new_versions[package] = lv, udays
        if lv != LATEST_VERSIONS[package]:
            print(f"{package[:24]:<24s} {LATEST_VERSIONS[package]:>12s} {cdays:4d} days ago")
            print(f"\x1B[91m >> update            to {lv:>12s} {udays:4d} days ago\x1B[0m")
            if udays > cdays:
                print("\x1B[7;91m >> WARNING POSSIBLE YANKED VERSION\x1B[0m")
        elif cdays > time:
            print(f"\x1B[104m{package[:24]:<24s} {LATEST_VERSIONS[package]:>12s} {cdays:4d} days ago\x1B[0m")

    print("all dependencies scanned.")
    return new_versions


def update_versions(time: int, up_days: int) -> None:
    """
    Use versions retrieved in read_versions to update the file dependencies.py
    Use ONLY if the test_latest workflow is completely validated on the CI
    """
    new_versions = read_versions(time)
    with open(".circleci/dependencies.py", "w") as dep_file:
        print("# This file is updated by manage_dependencies.py", file=dep_file)
        print("# Any new dependency can be added directly in this file\n", file=dep_file)
        print("LATEST_VERSIONS = {", file=dep_file)
        for package, (version, days) in sorted(new_versions.items(), key=lambda s: s[0].lower()):
            print(f'    "{package}": "{version if days>= up_days else LATEST_VERSIONS[package]}",', file=dep_file)
        print("}", file=dep_file)
    print("dependencies updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="manage_dependencies", description="Check and update versions of dependencies"
    )
    parser.add_argument(
        "-u",
        "--update",
        type=int,
        help=(
            "update dependencies.py older than DAYS days. A proper commit is required after that action. 0 will update"
            " everything."
        ),
        default=ETERNITY,
    )
    parser.add_argument(
        "-t", "--time", type=int, help="minimum time to show a package as frozen", default=ETERNITY, metavar="DAYS"
    )
    args = parser.parse_args()
    if args.update < ETERNITY:
        update_versions(args.time, args.update)
    else:
        read_versions(args.time)
