"""Where ddtrace looks for the libddwaf shared library.

A package normally bundles the library for its target architecture, and the
loader uses that copy.  A build can opt out of bundling it
(``build_py --no-bundle-libddwaf``, see docs/build_system.rst); the loader then
asks the dynamic linker for the SONAME instead, so the library installed on the
system is used.  That is what distribution packages need: they build from source
with no network access and package libddwaf separately.

setup.py only writes the bundled layout, so this module is where the loader's
expectations about it live.
"""

import os
import typing as t


FILE_EXTENSIONS = {"Linux": "so", "Darwin": "dylib", "Windows": "dll"}

TRANSLATE_ARCH = {"amd64": "x64", "i686": "x86_64", "x86": "win32"}

# The ctypes bindings in ddtrace.appsec._ddwaf follow the libddwaf 2.x C ABI, so
# the SONAME asked of the dynamic linker is the one of that major version.
ABI_MAJOR = 2


def target_arch(system: str, machine: str, is_64bit: bool = True) -> str:
    """Name of the directory holding the bundled library for a target platform."""
    arch = machine.lower()
    if system == "Windows" and arch == "amd64" and not is_64bit:
        arch = "x86"
    return TRANSLATE_ARCH.get(arch, arch)


def bundled_library_name(system: str) -> str:
    """File name of the library bundled in the package."""
    return "libddwaf." + FILE_EXTENSIONS[system]


def system_library_name(system: str) -> t.Optional[str]:
    """SONAME to ask the dynamic linker for, or None where there is no such convention."""
    if system == "Linux":
        return "libddwaf.so.%d" % ABI_MAJOR
    if system == "Darwin":
        return "libddwaf.%d.dylib" % ABI_MAJOR
    return None


def resolve_library(libddwaf_dir: str, system: str, machine: str, is_64bit: bool = True) -> str:
    """Path of the bundled library, or the SONAME to load when none is bundled."""
    bundled = os.path.join(libddwaf_dir, target_arch(system, machine, is_64bit), "lib", bundled_library_name(system))
    if os.path.exists(bundled):
        return bundled
    return system_library_name(system) or bundled


def is_loadable(library: str) -> bool:
    """Whether loading this library is worth attempting.

    A SONAME is resolved by the dynamic linker, so whether it is installed is
    only known once the load is attempted; a bundled path can be checked here.
    """
    return not os.path.isabs(library) or os.path.exists(library)
