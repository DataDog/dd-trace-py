import typing


Distribution = typing.NamedTuple("Distribution", [("name", str), ("version", str), ("path", str)])

_DISTRIBUTIONS = None  # type: typing.Optional[typing.Set[Distribution]]


def get_distributions():
    # type: () -> typing.Set[Distribution]
    """returns the name and version of all distributions in a python path"""
    global _DISTRIBUTIONS
    if _DISTRIBUTIONS is not None:
        return _DISTRIBUTIONS

    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    pkgs = set()
    for dist in importlib_metadata.distributions():
        # Get the root path of all files in a distribution
        path = str(dist.locate_file(""))
        # PKG-INFO and/or METADATA files are parsed when dist.metadata is accessed
        # Optimization: we should avoid accessing dist.metadata more than once
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]
        if name and version:
            pkgs.add(Distribution(path=path, name=name, version=version))

    _DISTRIBUTIONS = pkgs
    return _DISTRIBUTIONS
