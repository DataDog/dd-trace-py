from typing import TypedDict


Dependency = TypedDict("Dependency", {"name": str, "version": str})
"""
Stores the name and versions of python modules
"""


def create_dependency(name, version):
    # type: (str, str) -> Dependency
    """
    Helper for creating a Dependency dictionary
    """
    return {
        "name": name,
        "version": version,
    }
