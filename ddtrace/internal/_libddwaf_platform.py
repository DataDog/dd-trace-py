"""Where ddtrace looks for the libddwaf shared library.

A package normally bundles the library for its target architecture, and the
loader uses that copy.  A build can opt out of bundling it
(build_py --no-bundle-libddwaf, see docs/build_system.rst); the loader then asks
the dynamic linker for the library instead, so the one installed on the system
is used.  That is what distribution packages need: they build from source
with no network access and package libddwaf separately.

setup.py only writes the bundled layout, so this module is where the loader's
expectations about it live.  Only Linux has names to fall back to, which is why
setup.py rejects a build that bundles nothing on any other platform.
"""

import os


FILE_EXTENSIONS = {"Linux": "so", "Darwin": "dylib", "Windows": "dll"}

TRANSLATE_ARCH = {"amd64": "x64", "i686": "x86_64", "x86": "win32"}

# The ctypes bindings in ddtrace.appsec._ddwaf follow the libddwaf 2.x C ABI.
ABI_MAJOR = 2

# Names to ask the dynamic linker for when no library is bundled, in order.
# libddwaf's own CMake sets no SOVERSION, so an install built from upstream
# sources is plain libddwaf.so; a distribution that adds one ships
# libddwaf.so.<major> in its runtime package and keeps libddwaf.so for -devel.
# The versioned name therefore comes first, and ddwaf_get_version() is checked
# after the load, because an unversioned SONAME guarantees no ABI.
SYSTEM_LIBRARY_NAMES = {"Linux": ("libddwaf.so.%d" % ABI_MAJOR, "libddwaf.so")}


def target_arch(system: str, machine: str, is_64bit: bool = True) -> str:
    """Name of the directory holding the bundled library for a target platform."""
    arch = machine.lower()
    if system == "Windows" and arch == "amd64" and not is_64bit:
        arch = "x86"
    return TRANSLATE_ARCH.get(arch, arch)


def bundled_library_name(system: str) -> str:
    """File name of the library bundled in the package."""
    return "libddwaf." + FILE_EXTENSIONS[system]


def system_library_names(system: str) -> tuple[str, ...]:
    """Names to try on the dynamic linker, empty where no system library can be loaded."""
    return SYSTEM_LIBRARY_NAMES.get(system, ())


def resolve_library(libddwaf_dir: str, system: str, machine: str, is_64bit: bool = True) -> str:
    """Path of the bundled library, or the first name to try on the linker when none is bundled."""
    bundled = os.path.join(libddwaf_dir, target_arch(system, machine, is_64bit), "lib", bundled_library_name(system))
    if os.path.exists(bundled):
        return bundled
    names = system_library_names(system)
    return names[0] if names else bundled


def load_candidates(library: str, system: str) -> tuple[str, ...]:
    """Names to hand to ctypes, in order; a bundled library is the only candidate."""
    if os.path.isabs(library):
        return (library,)
    return system_library_names(system)


def is_loadable(library: str, system: str) -> bool:
    """Whether loading this library is worth attempting.

    A bundled library can be checked here; for a system one only the dynamic
    linker knows, so any name it may resolve counts as loadable.
    """
    if os.path.isabs(library):
        return os.path.exists(library)
    return library in system_library_names(system)
