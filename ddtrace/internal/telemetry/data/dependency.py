from typing import TypedDict


# Stores the name and versions of python modules
Dependency = TypedDict("Dependency", {"name": str, "version": str})


def create_dependency(name, version):
    # type: (str, str) -> Dependency
    """helper for creating a Dependency Dict"""
    return {
        "name": name,
        "version": version,
    }
