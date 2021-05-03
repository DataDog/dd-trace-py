import typing

import packaging.version


def parse_version(version):
    # type: (str) -> typing.Tuple[int, int, int]
    """Convert a version string to a tuple of (major, minor, micro)"""
    # If we have any spaces/extra text, grab the first part
    #   "1.0.0 beta1" -> "1.0.0"
    #   "1.0.0" -> "1.0.0"
    if " " in version:
        version = version.split()[0]

    # DEV: Do not use `packaging.version.parse`, we do not want a LegacyVersion here
    # This will raise an InvalidVersion exception if it cannot be parsed
    _version = packaging.version.Version(version)
    return (
        # Version.release was added in 17.1
        # packaging >= 20.0 has `Version.{major,minor,micro}`, use the following
        # to support older versions of the library
        # https://github.com/pypa/packaging/blob/47d40f640fddb7c97b01315419b6a1421d2dedbb/packaging/version.py#L404-L417
        _version.release[0] if len(_version.release) >= 1 else 0,
        _version.release[1] if len(_version.release) >= 2 else 0,
        _version.release[2] if len(_version.release) >= 3 else 0,
    )
