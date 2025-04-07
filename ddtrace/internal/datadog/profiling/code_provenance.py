import json
import platform
import sysconfig
from typing import List

from ddtrace.internal.packages import get_distributions


class Library:

    def __init__(
        self,
        kind: str,
        name: str,
        version: str,
        paths: List[str],
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

        for dist in get_distributions():
            if dist.paths:
                self.libraries.append(Library(kind="library", name=dist.name, version=dist.version, paths=dist.paths))

    def to_dict(self):
        return {"v1": [lib.to_dict() for lib in self.libraries]}


def json_str_to_export():
    cp = CodeProvenance()
    return json.dumps(cp.to_dict())
