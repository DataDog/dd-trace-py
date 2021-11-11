# distutils: include_dirs = ddtrace/appsec/include
# distutils: library_dirs = ddtrace/appsec/lib
# distutils: libraries = ddwaf

import typing
cimport _libddwaf


def version():
    # type: () -> typing.Tuple[int, int, int]
    cdef _libddwaf.ddwaf_version version
    _libddwaf.ddwaf_get_version(&version)
    return (version.major, version.minor, version.patch)
