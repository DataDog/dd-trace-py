import json
import platform
import sysconfig
import typing as t

from ddtrace.internal.packages import _package_for_root_module_mapping


class Library:

    def __init__(
        self,
        kind: str,
        name: str,
        version: str,
        paths: t.List[str],
    ):
        self.kind = kind
        self.name = name
        self.version = version
        self.paths = paths

    def to_dict(self):
        return {"kind": self.kind, "name": self.name, "version": self.version, "paths": self.paths}


class CodeProvenance:
    def __init__(self):
        self.libraries = []
        self.libraries.append(
            Library(
                kind="standard library",
                name="stdlib",
                version=platform.python_version(),
                paths=[sysconfig.get_path("stdlib")],
            )
        )

        module_to_distribution = _package_for_root_module_mapping()

        libraries: t.Dict[str, Library] = {}

        for module, dist in module_to_distribution.items():
            name = dist.name
            # special case for __pycache__/filename.cpython-3xx.pyc -> filename.py
            if module.startswith("__pycache__/"):
                module = module[len("__pycache__/") :].split(".")[0] + ".py"

            lib = libraries.get(name)
            if lib is None:
                lib = Library(kind="library", name=name, version=dist.version, paths=[])
                libraries[name] = lib
            lib.paths.append(module)

        self.libraries.extend(libraries.values())

    def to_dict(self):
        return {"v1": [lib.to_dict() for lib in self.libraries]}


def json_str_to_export():
    cp = CodeProvenance()
    return json.dumps(cp.to_dict())
