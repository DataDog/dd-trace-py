from typing import TypedDict


Dependency = TypedDict("Dependency", {"name": str, "version": str})
"""
Stores name and versions of imported modules 
"""


def create_dependency(name, version):
    # type: (str, str) -> Dependency
    return {
        "name": name,
        "version": version,
    }
