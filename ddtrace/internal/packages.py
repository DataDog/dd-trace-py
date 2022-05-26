import typing


Distribution = typing.NamedTuple("Distribution", [("name", str), ("version", str)])

_DISTRIBUTIONS = None


def get_distributions():
    # type: () -> typing.List[Distribution]
    global _DISTRIBUTIONS
    if _DISTRIBUTIONS is not None:
        return _DISTRIBUTIONS

    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    pkgs = []
    pkg_names = set()
    for dist in importlib_metadata.distributions():
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]
        if name and version and name not in pkg_names:
            pkgs.append(Distribution(name, version))
            # Avoids adding duplicates
            pkg_names.add(name)

    _DISTRIBUTIONS = pkgs
    return _DISTRIBUTIONS
