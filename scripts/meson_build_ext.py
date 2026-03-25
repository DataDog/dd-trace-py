#!/usr/bin/env python3
"""
meson_build_ext.py — helper script for meson custom_target extensions in dd-trace-py.

Subcommands:
  rust      Build the Rust (_native) extension via cargo
  cmake     Build a CMake extension component
  psutil    Build the vendored psutil extension
  libddwaf  Download the pre-built libddwaf binary

This script is called by meson custom_target() entries in meson.build.
Each invocation is for one component; meson handles parallelism and ordering.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


CURRENT_OS = platform.system()


def get_cmake_binary():
    """Find cmake binary: prefer pip cmake package, fall back to system cmake."""
    try:
        import cmake as cmake_pkg

        cmake_bin = Path(cmake_pkg.CMAKE_BIN_DIR) / "cmake"
        if cmake_bin.exists():
            return str(cmake_bin.resolve())
    except ImportError:
        pass
    return shutil.which("cmake") or "cmake"


def run(cmd, **kwargs):
    print(f"[meson_build_ext] Running: {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


# ---------------------------------------------------------------------------
# Rust build
# ---------------------------------------------------------------------------


def cmd_rust(args):
    """Build the Rust (_native) PyO3 extension via cargo."""
    cargo_target_dir = Path(args.cargo_target_dir)
    manifest = Path(args.manifest)
    output = Path(args.output)
    features = args.features
    python = args.python
    host = args.host

    # If cargo is not available, skip with a warning
    cargo_bin = shutil.which("cargo")
    if not cargo_bin:
        print("[meson_build_ext] WARNING: cargo not found, skipping Rust extension build")
        _write_stamp(args)
        return

    output.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(cargo_target_dir)
    env["PYO3_PYTHON"] = python
    # Prefer stable toolchain to avoid nightly/MSRV mismatches
    if not env.get("RUSTUP_TOOLCHAIN"):
        env["RUSTUP_TOOLCHAIN"] = "stable"

    # Build with cargo
    cargo_cmd = [
        cargo_bin,
        "build",
        "--release",
        "--manifest-path", str(manifest),
    ]
    if features:
        cargo_cmd += ["--features", features]

    run(cargo_cmd, env=env)

    # Locate the built shared library in cargo's release directory
    release_dir = cargo_target_dir / "release"

    # Determine the library filename cargo produces
    if host == "darwin" or CURRENT_OS == "Darwin":
        candidates = [
            release_dir / ("lib_native" + args.ext_suffix),
            release_dir / "lib_native.dylib",
        ]
    elif host == "windows" or CURRENT_OS == "Windows":
        candidates = [
            release_dir / "_native.dll",
            release_dir / "lib_native.dll",
        ]
    else:
        candidates = [
            release_dir / ("lib_native" + args.ext_suffix),
            release_dir / "lib_native.so",
        ]

    lib_file = None
    for candidate in candidates:
        if candidate.exists():
            lib_file = candidate
            break

    if lib_file is None:
        # Try glob
        matches = list(release_dir.glob("*_native*"))
        print(f"[meson_build_ext] Release dir contents: {list(release_dir.iterdir())}")
        raise RuntimeError(
            f"Could not find Rust-built _native library in {release_dir}. "
            f"Tried: {candidates}"
        )

    print(f"[meson_build_ext] Rust built: {lib_file} → {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lib_file, output)

    # Fix shared library identity
    if (host == "linux" or CURRENT_OS == "Linux") and shutil.which("patchelf"):
        run(["patchelf", "--set-soname", output.name, str(output)])
    elif (host == "darwin" or CURRENT_OS == "Darwin") and shutil.which("install_name_tool"):
        run(["install_name_tool", "-id", output.name, str(output)])

    # Run dedup_headers on profiling-generated headers (needed for CMake builds)
    if "profiling" in (features or ""):
        include_dir = cargo_target_dir / "include" / "datadog"
        if include_dir.exists():
            _ensure_dedup_headers()
            dedup = shutil.which("dedup_headers")
            if dedup:
                run([dedup, "common.h", "profiling.h"], cwd=str(include_dir))
            else:
                print("[meson_build_ext] WARNING: dedup_headers not found, skipping")
        else:
            print(f"[meson_build_ext] WARNING: Include dir not found: {include_dir}")

    _write_stamp(args)


def _write_stamp(args):
    """Write the meson stamp file to mark this target as complete."""
    stamp = getattr(args, "stamp", None)
    if stamp:
        stamp_path = Path(stamp)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text("done\n")


def _ensure_dedup_headers():
    """Install dedup_headers if not already available."""
    if shutil.which("dedup_headers"):
        return
    print("[meson_build_ext] Installing dedup_headers from libdatadog...")
    cargo = shutil.which("cargo") or "cargo"
    subprocess.run(
        [
            cargo, "install",
            "--git", "https://github.com/DataDog/libdatadog",
            "--bin", "dedup_headers",
            "tools",
        ],
        check=True,
    )


# ---------------------------------------------------------------------------
# CMake builds
# ---------------------------------------------------------------------------


def cmd_cmake(args):
    """Build a CMake extension component."""
    cmake = get_cmake_binary()

    # Check if cmake is available
    if cmake == "cmake" and not shutil.which("cmake"):
        # cmake not found even as fallback — check pip cmake
        try:
            import cmake as cmake_pkg  # noqa: F401
        except ImportError:
            print(f"[meson_build_ext] WARNING: cmake not found, skipping {args.component_name}")
            _write_stamp(args)
            return

    src_root = Path(args.src_root)
    cmake_src_dir = Path(args.cmake_src_dir)
    output = Path(args.output)
    install_dir = Path(args.install_dir)
    component_name = args.component_name
    build_type = args.build_type or "RelWithDebInfo"

    output.parent.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)

    # Build directory alongside source
    build_dir = src_root / ".mesonbuild" / "cmake" / component_name
    build_dir.mkdir(parents=True, exist_ok=True)

    python_root = Path(args.py_prefix).resolve()

    cmake_args = [
        cmake,
        f"-S{cmake_src_dir}",
        f"-B{build_dir}",
        f"-DPython3_ROOT_DIR={python_root}",
        f"-DPython3_EXECUTABLE={args.python}",
        f"-DPYTHON_EXECUTABLE={args.python}",
        f"-DCMAKE_BUILD_TYPE={build_type}",
        f"-DLIB_INSTALL_DIR={install_dir}",
        f"-DEXTENSION_NAME={output.name}",  # full filename e.g. _memalloc.cpython-314-darwin.so
        f"-DEXTENSION_SUFFIX={args.ext_suffix}",
    ]

    # FetchContent cache for Abseil etc.
    fetchcontent_dir = src_root / ".download_cache" / "_cmake_deps"
    fetchcontent_dir.mkdir(parents=True, exist_ok=True)
    cmake_args.append(f"-DFETCHCONTENT_BASE_DIR={fetchcontent_dir}")

    # Native extension location (for dd_wrapper and extensions that need _native)
    if args.native_ext_dir:
        cmake_args.append(f"-DNATIVE_EXTENSION_LOCATION={args.native_ext_dir}")

    # Rust-generated headers
    if args.rust_headers_dir:
        cmake_args.append(f"-DRUST_GENERATED_HEADERS_DIR={args.rust_headers_dir}")

    # dd_wrapper location (for _memalloc, _ddup, _stack)
    if args.dd_wrapper_dir:
        cmake_args.append(f"-DDD_WRAPPER_DIR={args.dd_wrapper_dir}")

    # sccache support
    sccache_path = os.getenv("DD_SCCACHE_PATH")
    if sccache_path:
        cc_old = os.getenv("DD_CC_OLD", shutil.which("cc") or "cc")
        cxx_old = os.getenv("DD_CXX_OLD", shutil.which("c++") or "c++")
        cmake_args += [
            f"-DCMAKE_C_COMPILER={cc_old}",
            f"-DCMAKE_C_COMPILER_LAUNCHER={sccache_path}",
            f"-DCMAKE_CXX_COMPILER={cxx_old}",
            f"-DCMAKE_CXX_COMPILER_LAUNCHER={sccache_path}",
        ]

    run(cmake_args)

    # Build
    parallel = os.cpu_count() or 4
    build_args = [cmake, "--build", str(build_dir), "--config", build_type, f"-j{parallel}"]
    run(build_args)

    # Install
    install_args = [cmake, "--install", str(build_dir), "--config", build_type]
    run(install_args)

    # Verify the output was produced
    if not output.exists():
        # Check if it's in the install_dir with a different name
        candidates = list(install_dir.glob(f"*{args.ext_suffix}"))
        if candidates:
            shutil.copy2(candidates[0], output)
        else:
            raise RuntimeError(
                f"CMake build of {component_name} did not produce expected output: {output}\n"
                f"Install dir contents: {list(install_dir.iterdir())}"
            )

    _write_stamp(args)


# ---------------------------------------------------------------------------
# psutil vendor build
# ---------------------------------------------------------------------------


def cmd_psutil(args):
    """Build the vendored psutil extension by importing its get_extensions()."""
    import importlib.util
    src_root = Path(args.src_root)
    psutil_dir = src_root / "ddtrace" / "vendor" / "psutil"
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    build_dir = src_root / ".mesonbuild" / "psutil_build"
    build_dir.mkdir(parents=True, exist_ok=True)

    # Import psutil's setup.py as a module to call get_extensions() without
    # invoking main() (which fails due to missing convert_readme.py in vendor tree).
    spec = importlib.util.spec_from_file_location(
        "ddtrace.vendor.psutil.setup", psutil_dir / "setup.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    exts = mod.get_extensions()

    if not exts:
        print("[meson_build_ext] WARNING: psutil get_extensions() returned empty list, skipping")
        output.write_text("# placeholder\n")
        _write_stamp(args)
        return

    # Build via setuptools build_ext programmatically
    # (distutils was removed in Python 3.12; use setuptools)
    from setuptools import Distribution
    from setuptools.command.build_ext import build_ext as _build_ext

    dist = Distribution({"ext_modules": exts})
    cmd = _build_ext(dist)
    cmd.build_lib = str(build_dir)
    cmd.build_temp = str(build_dir / "temp")
    cmd.inplace = True
    # Tell distutils the source root so relative paths resolve correctly
    import os as _os
    orig_dir = _os.getcwd()
    _os.chdir(str(src_root))
    try:
        cmd.ensure_finalized()
        cmd.run()
    finally:
        _os.chdir(orig_dir)

    # Locate the built .so
    host = args.host
    if host == "darwin" or CURRENT_OS == "Darwin":
        pattern = "_psutil_osx*"
    elif host == "linux" or CURRENT_OS == "Linux":
        pattern = "_psutil_linux*"
    elif host == "windows" or CURRENT_OS == "Windows":
        pattern = "_psutil_windows*"
    else:
        pattern = "_psutil_*"

    # Filter to only shared libraries (exclude .c/.h source files)
    candidates = [p for p in psutil_dir.glob(pattern) if p.suffix in (".so", ".pyd", ".dylib") or p.name.endswith(args.ext_suffix)]
    if not candidates:
        candidates = [p for p in psutil_dir.glob(f"*{args.ext_suffix}") if p.is_file()]

    if not candidates:
        print("[meson_build_ext] WARNING: psutil extension not found, skipping")
        output.write_text("# placeholder\n")
        _write_stamp(args)
        return

    src = candidates[0].resolve()
    dst = output.resolve()
    if src != dst:
        shutil.copy2(src, dst)
        print(f"[meson_build_ext] psutil built: {src} → {dst}")
    else:
        print(f"[meson_build_ext] psutil built in-place: {dst}")
    _write_stamp(args)


# ---------------------------------------------------------------------------
# libddwaf download
# ---------------------------------------------------------------------------


def cmd_libddwaf(args):
    """Download libddwaf pre-built binary using pure stdlib (no setuptools required)."""
    import hashlib
    import tarfile
    from urllib.request import urlretrieve

    src_root = Path(args.src_root)
    output = Path(args.output)
    host = args.host.lower() if args.host else CURRENT_OS.lower()

    LIBDDWAF_VERSION = "1.30.1"
    URL_ROOT = "https://github.com/DataDog/libddwaf/releases/download"
    DOWNLOAD_DIR = src_root / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf"
    CACHE_DIR = src_root / ".download_cache"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # If libddwaf is already downloaded, skip
    if DOWNLOAD_DIR.exists() and any(DOWNLOAD_DIR.iterdir()):
        print(f"[meson_build_ext] libddwaf already present at {DOWNLOAD_DIR}, skipping download")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("downloaded\n")
        return

    # Determine OS name for URL
    if host == "darwin":
        os_name = "darwin"
        suffix = ".dylib"
        archs = ["arm64", "x86_64"]
    elif host == "linux":
        os_name = "linux"
        suffix = ".so"
        archs = ["aarch64", "x86_64"]
    elif host == "windows":
        os_name = "windows"
        suffix = ".dll"
        archs = ["arm64", "win32", "x64"]
    else:
        print(f"[meson_build_ext] WARNING: unsupported host '{host}', skipping libddwaf download")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("skipped\n")
        return

    # Only download the matching architecture
    import struct
    machine = platform.machine().lower()
    if host == "darwin":
        if machine in ("arm64", "aarch64"):
            archs = ["arm64"]
        else:
            archs = ["x86_64"]
    elif host == "linux":
        if machine in ("arm64", "aarch64"):
            archs = ["aarch64"]
        else:
            archs = ["x86_64"]

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for arch in archs:
        arch_dir = DOWNLOAD_DIR / arch
        if arch_dir.is_dir() and any(arch_dir.iterdir()):
            print(f"[meson_build_ext] libddwaf/{arch} already present, skipping")
            continue

        if host == "linux":
            archive_name = f"libddwaf-{LIBDDWAF_VERSION}-{arch}-linux-musl.tar.gz"
        else:
            archive_name = f"libddwaf-{LIBDDWAF_VERSION}-{os_name}-{arch}.tar.gz"

        url = f"{URL_ROOT}/{LIBDDWAF_VERSION}/{archive_name}"
        cached = CACHE_DIR / archive_name

        if not cached.exists():
            print(f"[meson_build_ext] Downloading {archive_name} to {cached}")
            urlretrieve(url, str(cached))
        else:
            print(f"[meson_build_ext] Using cached {cached}")

        # Extract dynamic libraries from the tarball
        package_dir = f"libddwaf-{LIBDDWAF_VERSION}-{os_name}-{arch}"
        if host == "linux":
            package_dir = f"libddwaf-{LIBDDWAF_VERSION}-{arch}-linux-musl"

        with tarfile.open(str(cached), "r:gz") as tar:
            members = [m for m in tar.getmembers() if m.name.endswith((suffix, ".so"))]
            tar.extractall(members=members, path=str(src_root))

        extracted_dir = src_root / package_dir
        if extracted_dir.exists():
            extracted_dir.rename(arch_dir)

        # Rename ddwaf.xxx → libddwaf.xxx
        lib_dir = arch_dir / "lib"
        if lib_dir.exists():
            for f in lib_dir.iterdir():
                if f.name.startswith("ddwaf") and not f.name.startswith("libddwaf"):
                    f.rename(lib_dir / ("lib" + f.name))

    print(f"[meson_build_ext] libddwaf downloaded to {DOWNLOAD_DIR}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("downloaded\n")
    print(f"[meson_build_ext] libddwaf download stamp written: {output}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="meson extension build helper for dd-trace-py")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # --- rust ---
    p_rust = sub.add_parser("rust", help="Build Rust extension")
    p_rust.add_argument("--src-root", required=True)
    p_rust.add_argument("--cargo-target-dir", required=True)
    p_rust.add_argument("--manifest", required=True)
    p_rust.add_argument("--features", default="")
    p_rust.add_argument("--ext-suffix", required=True)
    p_rust.add_argument("--output", required=True)
    p_rust.add_argument("--stamp", default="")
    p_rust.add_argument("--python", required=True)
    p_rust.add_argument("--host", default=CURRENT_OS.lower())

    # --- cmake ---
    p_cmake = sub.add_parser("cmake", help="Build a CMake extension")
    p_cmake.add_argument("--src-root", required=True)
    p_cmake.add_argument("--cmake-src-dir", required=True)
    p_cmake.add_argument("--output", required=True)
    p_cmake.add_argument("--install-dir", required=True)
    p_cmake.add_argument("--stamp", default="")
    p_cmake.add_argument("--python", required=True)
    p_cmake.add_argument("--py-prefix", required=True)
    p_cmake.add_argument("--ext-suffix", required=True)
    p_cmake.add_argument("--build-type", default="RelWithDebInfo")
    p_cmake.add_argument("--cargo-target-dir", default="")
    p_cmake.add_argument("--rust-headers-dir", default="")
    p_cmake.add_argument("--component-name", required=True)
    p_cmake.add_argument("--native-ext-dir", default="")
    p_cmake.add_argument("--dd-wrapper-dir", default="")

    # --- psutil ---
    p_psutil = sub.add_parser("psutil", help="Build psutil vendor extension")
    p_psutil.add_argument("--src-root", required=True)
    p_psutil.add_argument("--output", required=True)
    p_psutil.add_argument("--stamp", default="")
    p_psutil.add_argument("--python", required=True)
    p_psutil.add_argument("--ext-suffix", required=True)
    p_psutil.add_argument("--host", default=CURRENT_OS.lower())

    # --- libddwaf ---
    p_waf = sub.add_parser("libddwaf", help="Download libddwaf binary")
    p_waf.add_argument("--src-root", required=True)
    p_waf.add_argument("--output", required=True)
    p_waf.add_argument("--python", required=True)
    p_waf.add_argument("--host", default=CURRENT_OS.lower())

    args = parser.parse_args()

    dispatch = {
        "rust": cmd_rust,
        "cmake": cmd_cmake,
        "psutil": cmd_psutil,
        "libddwaf": cmd_libddwaf,
    }
    try:
        dispatch[args.subcommand](args)
    except subprocess.CalledProcessError as e:
        # Check if the component is optional (stamp arg present means we can skip)
        stamp = getattr(args, "stamp", None)
        if stamp:
            print(
                f"[meson_build_ext] WARNING: {args.subcommand} build failed (exit {e.returncode}), "
                f"writing skip stamp. Some functionality may be unavailable."
            )
            _write_stamp(args)
        else:
            raise


if __name__ == "__main__":
    main()
