import ast
from io import StringIO
import os
import pathlib
import sys
import typing

from pip import _internal


sys.path.append(str(pathlib.Path(__file__).parent.parent.resolve()))
from riotfile import SUPPORTED_PYTHON_VERSIONS  # noqa:E402


CONTRIB_ROOT = "ddtrace/contrib"


class Capturing(list):
    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._stringio = StringIO()
        return self

    def __exit__(self, *args):
        self.extend(self._stringio.getvalue().splitlines())
        del self._stringio  # free up some memory
        sys.stdout = self._stdout


def _get_integrated_modules() -> typing.Set[str]:
    """Get all modules that have contribs implemented for them"""
    all_required_modules = set()
    for item in os.listdir(CONTRIB_ROOT):
        contrib_dir = f"{CONTRIB_ROOT}/{item}"
        init_filepath = f"{contrib_dir}/__init__.py"
        if os.path.isdir(contrib_dir) and os.path.isfile(init_filepath):
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


def _get_version_extremes(module: str) -> typing.Dict[str, typing.Dict[str, str]]:
    with Capturing() as output:
        _internal.main(["index", "versions", module])
    # https://stackoverflow.com/a/66579111/735204


def main():
    all_required_modules = _get_integrated_modules()
    envs = _get_riot_envs_including_any(all_required_modules)
    for module in all_required_modules:
        versions = _get_version_extremes(module)
        print(versions)
    # for each required_modules
    #  get earliest and latest pypi versions
    #  for each env in set
    #   get version of module used
    pass


if __name__ == "__main__":
    main()
