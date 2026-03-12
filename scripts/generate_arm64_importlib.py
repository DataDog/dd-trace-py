"""Generate ARM64 Python import libraries from x64 DLL exports.

Must run inside a vcvarsall x64_arm64 environment (dumpbin and lib.exe must be on PATH).

For each Python import library needed (python3XX.lib, python3.lib), this script:
  1. Finds the matching DLL in the Python install
  2. Uses dumpbin /exports to list exported symbol names
  3. Writes a .def file from those exports
  4. Uses lib.exe /machine:ARM64 to produce an ARM64 import library
  5. Overwrites the x64 .lib in the Python install's libs/ directory

This is needed for cross-compiling C/Cython extensions targeting win_arm64 from an
x64 host Python, because the host Python ships x64 import libraries, but MSVC cross-
linker (HostX64/arm64) requires ARM64-tagged import libraries.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def find_python_libs_dir():
    """Return the libs/ directory inside the Python install."""
    import sysconfig

    libs = Path(sysconfig.get_config_var("installed_platbase") or sys.prefix) / "libs"
    if libs.is_dir():
        return libs
    # Fallback: search from sys.prefix
    for candidate in [
        Path(sys.prefix) / "libs",
        Path(sys.prefix) / "Libs",
    ]:
        if candidate.is_dir():
            return candidate
    raise RuntimeError(f"Cannot find Python libs/ directory under {sys.prefix}")


def find_python_dll(dll_name, search_dirs):
    """Find a Python DLL by name in the given search directories."""
    for d in search_dirs:
        candidate = Path(d) / dll_name
        if candidate.is_file():
            return candidate
    return None


def get_exports_from_dll(dll_path):
    """Use dumpbin /exports to get the exported symbol names from a DLL."""
    result = subprocess.run(
        ["dumpbin", "/exports", str(dll_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    exports = []
    # dumpbin output has lines like:
    #   ordinal  hint   RVA       name
    #       1    0  00001234  PyArg_Parse
    in_exports = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("ordinal") and "name" in stripped:
            in_exports = True
            continue
        if in_exports:
            # Blank line ends the export section
            if not stripped:
                if exports:
                    break
                continue
            # Match lines with 4 columns: ordinal hint RVA name
            m = re.match(r"^\s*\d+\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+(\S+)", line)
            if m:
                exports.append(m.group(1))
    return exports


def write_def_file(def_path, dll_name, exports):
    """Write a .def file listing the exports."""
    with open(def_path, "w") as f:
        f.write(f"LIBRARY {dll_name}\n")
        f.write("EXPORTS\n")
        for sym in exports:
            f.write(f"    {sym}\n")


def generate_arm64_importlib(dll_path, lib_output_path):
    """Generate an ARM64 import library for the given DLL."""
    dll_name = Path(dll_path).name
    with tempfile.TemporaryDirectory() as tmpdir:
        def_path = Path(tmpdir) / (Path(dll_path).stem + ".def")

        print(f"  Extracting exports from {dll_path} ...")
        exports = get_exports_from_dll(dll_path)
        if not exports:
            print(f"  WARNING: No exports found in {dll_path}, skipping")
            return

        print(f"  Found {len(exports)} exports")
        write_def_file(def_path, dll_name, exports)

        print(f"  Generating ARM64 import lib: {lib_output_path}")
        subprocess.run(
            [
                "lib",
                f"/def:{def_path}",
                f"/out:{lib_output_path}",
                "/machine:ARM64",
                "/nologo",
            ],
            check=True,
        )
        print(f"  Done: {lib_output_path}")


def main():
    ver = sys.version_info
    major, minor = ver.major, ver.minor

    libs_dir = find_python_libs_dir()
    print(f"Python libs dir: {libs_dir}")

    # Directories to search for the DLLs
    python_dir = Path(sys.prefix)
    search_dirs = [python_dir, python_dir / "DLLs"]

    # Process python3XX.dll -> python3XX.lib
    versioned_dll_name = f"python{major}{minor}.dll"
    versioned_lib_path = libs_dir / f"python{major}{minor}.lib"

    dll = find_python_dll(versioned_dll_name, search_dirs)
    if dll is None:
        # Some distributions put the DLL alongside python.exe
        exe_dir = Path(sys.executable).parent
        dll = find_python_dll(versioned_dll_name, [exe_dir])
    if dll is None:
        print(f"ERROR: Could not find {versioned_dll_name} — tried {search_dirs}")
        sys.exit(1)

    generate_arm64_importlib(dll, versioned_lib_path)

    # Process python3.dll -> python3.lib (stable ABI)
    stable_dll_name = f"python{major}.dll"
    stable_lib_path = libs_dir / f"python{major}.lib"

    stable_dll = find_python_dll(stable_dll_name, search_dirs + [Path(sys.executable).parent])
    if stable_dll is not None:
        generate_arm64_importlib(stable_dll, stable_lib_path)
    else:
        print(f"  Note: {stable_dll_name} not found, skipping stable ABI import lib")

    print("ARM64 import library generation complete.")


if __name__ == "__main__":
    main()
