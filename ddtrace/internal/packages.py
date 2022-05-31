import typing


Distribution = typing.NamedTuple("Distribution", [("name", str), ("version", str)])

_DISTRIBUTIONS = None


def _get_dist_metadata(dist):
    for fname in ("METADATA", "PKG-INFO"):
        file_path = dist._path / fname
        if not file_path.exists():
            continue

        name = None
        version = None
        with file_path.open() as fp:
            for line in fp:
                if line.startswith("Name:"):
                    _, _, name = line.partition(":")
                    name = name.strip()
                elif line.startswith("Version:"):
                    _, _, version = line.partition(":")
                    version = version.strip()

                if name and version:
                    return name, version
    return None, None


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
        name, version = _get_dist_metadata(dist)
        if name and version and name not in pkg_names:
            pkgs.append(Distribution(name, version))
            # Avoids adding duplicates
            pkg_names.add(name)

    _DISTRIBUTIONS = pkgs
    return _DISTRIBUTIONS
