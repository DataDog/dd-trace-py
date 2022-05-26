import typing


Distribution = typing.NamedTuple("Distribution", [("name", str), ("version", str)])

_DISTRIBUTIONS = None  # type: typing.Optional[typing.Set[Distribution]]


def get_distributions():
    # type: () -> typing.Set[Distribution]
    global _DISTRIBUTIONS
    if _DISTRIBUTIONS is not None:
        return _DISTRIBUTIONS

    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    pkgs = set()
    for dist in importlib_metadata.distributions():
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]
        if name and version:
            # add distribution to a set to remove duplicates
            pkgs.add(Distribution(name, version))

    _DISTRIBUTIONS = pkgs
    return _DISTRIBUTIONS
