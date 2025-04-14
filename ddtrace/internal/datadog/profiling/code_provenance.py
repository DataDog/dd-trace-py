import importlib.util
import json
from pathlib import Path
import platform
import sys
import sysconfig
import typing as t

from ddtrace.internal.packages import _package_for_root_module_mapping


class Library:

    def __init__(
        self,
        kind: str,
        name: str,
        version: str,
        paths: t.Set[str],
    ):
        self.kind = kind
        self.name = name
        self.version = version
        self.paths = paths

    def to_dict(self):
        return {"kind": self.kind, "name": self.name, "version": self.version, "paths": list(self.paths)}


class CodeProvenance:
    def __init__(self):
        self.libraries = []

        python_stdlib = Library(
            kind="standard library",
            name="stdlib",
            version=platform.python_version(),
            paths=set(
                [
                    sysconfig.get_path("stdlib"),
                    # Though we do handle frozen modules in the stdlib, these
                    # two modules appear as _frozen_importlib and _frozen_importlib_external
                    # in sys.stdlib_module_names where they appear as below
                    # from profiles, we hardcode them here.
                    "<frozen importlib._bootstrap>",
                    "<frozen importlib._bootstrap_external>",
                ]
            ),
        )

        # Add frozen modules that are part of the standard library
        # This is mainly to handle locations like <frozen importlib._bootstrap>.
        # sys.stdlib_module_names was added in Python 3.10
        # For older versions, we could iterate over sys.modules.keys(), but that
        # would include all modules, not just stdlib modules.
        if sys.version_info >= (3, 10):
            for name in sys.stdlib_module_names:
                try:
                    spec = importlib.util.find_spec(name)
                    if spec and spec.origin == "frozen":
                        python_stdlib.paths.add(f"<frozen {spec.name}>")
                except Exception:
                    continue

        self.libraries.append(python_stdlib)

        module_to_distribution = _package_for_root_module_mapping()

        libraries: t.Dict[str, Library] = {}

        site_packages = Path(sysconfig.get_path("purelib"))
        for module, dist in module_to_distribution.items():
            name = dist.name
            # special case for __pycache__/filename.cpython-3xx.pyc -> filename.py
            if module.startswith("__pycache__/"):
                module = module[len("__pycache__/") :].split(".")[0] + ".py"

            lib = libraries.get(name)
            if lib is None:
                lib = Library(kind="library", name=name, version=dist.version, paths=set())
                libraries[name] = lib

            # We assume that each module is a directory or a python file
            # relative to site-packages/ directory.
            module_path = site_packages / module
            if module.endswith(".py") or module_path.is_dir():
                lib.paths.add(str(module_path))

        self.libraries.extend(libraries.values())

    def to_dict(self):
        return {"v1": [lib.to_dict() for lib in self.libraries]}


def json_str_to_export():
    cp = CodeProvenance()
    return json.dumps(cp.to_dict())
