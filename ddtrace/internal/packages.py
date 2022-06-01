import typing


Distribution = typing.NamedTuple("Distribution", [("name", str), ("version", str)])

_DISTRIBUTIONS = None  # type: typing.Optional[typing.Set[Distribution]]


def get_distributions():
    # type: () -> typing.Set[Distribution]
    """returns the names, versions and path of all installed distributions"""
    global _DISTRIBUTIONS
    if _DISTRIBUTIONS is not None:
        return _DISTRIBUTIONS

    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    pkgs = set()
    for dist in importlib_metadata.distributions():
        # accessing dist.metadata reads all lines in the PKG-INFO and/or METADATA files
        # We should avoid accessing dist.metadata more than once
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]
        if name and version:
            pkgs.add(Distribution(name, version))

    _DISTRIBUTIONS = pkgs
    return _DISTRIBUTIONS
