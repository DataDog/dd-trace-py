import csv
from typing import Dict
from typing import Set

from packaging.version import parse as parse_version

import riotfile


def tree_pkgs_from_riot() -> Dict[str, Set[str]]:

    return _tree_pkgs_from_riot(riotfile.venv)


def _tree_pkgs_from_riot(node: riotfile.Venv) -> Dict[str, Set]:
    result = {pkg: _format_version_specifiers(set(versions)) for pkg, versions in node.pkgs.items()}
    for child_venv in node.venvs:
        child_pkgs = _tree_pkgs_from_riot(child_venv)
        for pkg_name, versions in child_pkgs.items():
            if pkg_name in result:
                result[pkg_name] = result[pkg_name].union(versions)
            else:
                result[pkg_name] = versions
    return result


def _format_version_specifiers(spec: Set[str]) -> Set[str]:
    return set([part.strip("~<>==") for v in [v.split(",") for v in spec if v] for part in v if "!=" not in part])


def write_out(all_pkgs: Dict[str, Set[str]]) -> None:
    with open("min_versions.csv", "w") as csvfile:
        csv_writer = csv.writer(csvfile, delimiter=",")
        csv_writer.writerow(["pkg_name", "min_version"])
        for pkg, versions in sorted(all_pkgs.items()):
            min_version = "0"
            if versions:
                min_version = min((parse_version(v) for v in versions))
            print("%s\n\tTested versions: %s\n\tMinimum: %s" % (pkg, sorted(list(versions)), min_version))
            csv_writer.writerow([pkg, min_version])


def main():
    """Discover the minimum version of every package referenced in the riotfile

    Writes to stdout and min_versions.csv
    """
    write_out(tree_pkgs_from_riot())


main()
