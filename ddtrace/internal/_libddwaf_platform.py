"""On-disk layout of the libddwaf artifact shipped with ddtrace.

Both the build and the runtime need to agree on where the libddwaf library
lives, how the target architecture is named, and which libddwaf versions the
ctypes bindings in ddtrace.appsec._ddwaf can talk to.  setup.py loads this
module by file path (it cannot import ddtrace at build time) and
ddtrace.internal.settings.asm imports it normally, so the mapping cannot drift
between the two.

A build can either bundle the prebuilt library (the default) or record the
absolute path of a system-provided one in a plain text file
(DD_USE_SYSTEM_LIBDDWAF=1, see docs/build_system.rst).  A text file is used
instead of a symlink because symlinks do not survive wheel archiving.
"""

import os
import typing as t


FILE_EXTENSIONS = {"Linux": "so", "Darwin": "dylib", "Windows": "dll"}

TRANSLATE_ARCH = {"amd64": "x64", "i686": "x86_64", "x86": "win32"}

LINK_FILE_NAME = "libddwaf.link"

# The ctypes bindings follow the libddwaf 2.x C ABI. Anything else (1.x, or an
# unknown future major) is rejected at build time instead of crashing on load.
ABI_MAJOR = 2
ABI_MINIMUM = (2, 0, 0)


def target_arch(system: str, machine: str, is_64bit: bool = True) -> str:
    """Name of the directory holding the libddwaf artifact for a target platform.

    system and machine are platform.system() and platform.machine() values of
    the target, which is not the build machine for a cross build.
    """
    arch = machine.lower()
    if system == "Windows" and arch == "amd64" and not is_64bit:
        arch = "x86"
    return TRANSLATE_ARCH.get(arch, arch)


def library_name(system: str) -> str:
    """File name of the libddwaf shared library on a target platform."""
    return "libddwaf." + FILE_EXTENSIONS[system]


def library_dir(libddwaf_dir: str, arch: str) -> str:
    """Directory holding the artifact for one target architecture."""
    return os.path.join(libddwaf_dir, arch, "lib")


def read_link_file(lib_dir: str) -> t.Optional[str]:
    """Path recorded by a system-library build, or None if there is none."""
    try:
        with open(os.path.join(lib_dir, LINK_FILE_NAME)) as f:
            return f.read().strip() or None
    except OSError:
        return None


def resolve_library_path(libddwaf_dir: str, system: str, machine: str, is_64bit: bool = True) -> str:
    """Path of the libddwaf library to load.

    The bundled library wins; a path recorded by a system-library build is used
    only when no library is bundled.  The recorded path is returned as is: if
    the system library has since been removed, loading fails with that path in
    the error message.
    """
    lib_dir = library_dir(libddwaf_dir, target_arch(system, machine, is_64bit))
    filename = os.path.join(lib_dir, library_name(system))
    if not os.path.exists(filename):
        return read_link_file(lib_dir) or filename
    return filename


def parse_version(version: str) -> t.Optional[tuple[int, int, int]]:
    """Numeric part of a libddwaf version, or None if it cannot be read."""
    numbers = version.strip().split("+")[0].split("-")[0].split(".")
    if len(numbers) != 3:
        return None
    try:
        major, minor, patch = (int(n) for n in numbers)
    except ValueError:
        return None
    return major, minor, patch


def abi_error(version: str) -> t.Optional[str]:
    """Why a libddwaf of this version cannot be used, or None if it can."""
    parsed = parse_version(version)
    if parsed is None:
        return "cannot parse version %r" % version
    if parsed[0] != ABI_MAJOR or parsed < ABI_MINIMUM:
        return "ddtrace requires libddwaf >= %s and < %d.0.0" % (
            ".".join(str(n) for n in ABI_MINIMUM),
            ABI_MAJOR + 1,
        )
    return None


def stage_system_library(libddwaf_dir: str, arch: str, target: str) -> str:
    """Record target as the libddwaf to load, replacing any bundled artifact.

    Build time only.  The whole artifact directory is rebuilt from scratch so a
    package can never contain both a bundled library and a recorded path.
    """
    import shutil

    shutil.rmtree(libddwaf_dir, ignore_errors=True)
    lib_dir = library_dir(libddwaf_dir, arch)
    os.makedirs(lib_dir)
    link_file = os.path.join(lib_dir, LINK_FILE_NAME)
    with open(link_file, "w") as f:
        f.write(target + "\n")
    return link_file


def remove_link_files(libddwaf_dir: str) -> None:
    """Drop paths recorded by an earlier system-library build.

    Build time only, so that switching back to a bundled build cannot leave a
    stale recorded path behind.
    """
    for dirpath, _, filenames in os.walk(libddwaf_dir):
        if LINK_FILE_NAME in filenames:
            os.unlink(os.path.join(dirpath, LINK_FILE_NAME))
