import ast
import csv

from packaging.version import parse as parse_version


def tree_pkgs(node):
    result = {pkg: set(versions) for pkg, versions in node.pkgs.items()}
    for child_venv in node.venvs:
        child_pkgs = tree_pkgs(child_venv)
        for pkg_name, versions in child_pkgs.items():
            if pkg_name in result:
                result[pkg_name] = result[pkg_name].union(versions)
            else:
                result[pkg_name] = versions
    return result


def get_venv_root_ast_node(syntax_tree):
    for node in reversed(syntax_tree.body):
        if hasattr(node, "targets"):
            if node.targets[0].id == "venv":
                return node.value


def get_children(venv_ast_node):
    for keyword in getattr(venv_ast_node, "keywords", []):
        if keyword.arg == "venvs":
            return keyword.value.elts
    return []


def _parse_requirements_from_node(venv_ast_node):
    pkgs_node = None
    for keyword in getattr(venv_ast_node, "keywords", []):
        if keyword.arg == "pkgs":
            pkgs_node = keyword

    if not pkgs_node:
        return dict()

    requirements = dict()
    for key, value in zip(pkgs_node.value.keys, pkgs_node.value.values):
        versions = set()
        if isinstance(value, ast.Constant):
            versions = set([value.value])
        elif isinstance(value, ast.List):
            versions = [getattr(item, "value", "") for item in value.elts]
        requirements[key.value] = set(
            [part.strip("~<>==") for v in [v.split(",") for v in versions if v] for part in v if "!=" not in part]
        )
    return requirements


def _requirements_at_node(venv_ast_node):
    result = _parse_requirements_from_node(venv_ast_node)
    for child in get_children(venv_ast_node):
        child_pkgs = _requirements_at_node(child)
        for pkg_name, versions in child_pkgs.items():
            if pkg_name in result:
                result[pkg_name] = result[pkg_name].union(versions)
            else:
                result[pkg_name] = versions
    return result


def parse_riotfile_ast(riotfile_ast):
    root_venv_node = get_venv_root_ast_node(riotfile_ast)
    requirements = _requirements_at_node(root_venv_node)
    return requirements


def get_riotfile_requirements():
    with open("riotfile.py", "r") as f:
        riotfile_content = f.read()
    return parse_riotfile_ast(ast.parse(riotfile_content))


def main():
    """Discover the minimum version of every package referenced in the riotfile

    Writes to stdout and min_versions.csv
    """
    all_pkgs = get_riotfile_requirements()
    with open("min_versions.csv", "w") as csvfile:
        csv_writer = csv.writer(csvfile, delimiter=",")
        for pkg, versions in all_pkgs.items():
            min_version = "0"
            if versions:
                min_version = min((parse_version(v) for v in versions))
            print("%s\n\tTested versions: %s\n\tMinimum: %s" % (pkg, versions, min_version))
            csv_writer.writerow([pkg, min_version])


main()
