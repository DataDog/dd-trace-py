from typing import TypedDict


Dependency = TypedDict("Dependency", {"name": str, "version": str})
"""
Stores the name and versions of python modules
"""


def create_dependency(name, version):
    """
    Helper for creating a Dependency dictionary
    """
    # type: (str, str) -> Dependency
    return {
        "name": name,
        "version": version,
    }  # type: Dependency
