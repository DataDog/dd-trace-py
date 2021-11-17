from typing import TypedDict


# Stores the name and versions of python modules
Dependency = TypedDict("Dependency", {"name": str, "version": str})


def create_dependency(name, version):
    # type: (str, str) -> Dependency
    """
    Helper for creating a Dependency dictionary
    """
    return {
        "name": name,
        "version": version,
    }
