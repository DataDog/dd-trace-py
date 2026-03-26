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
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


CURRENT_OS = platform.system()
LIBDATADOG_REPO = "https://github.com/DataDog/libdatadog"
MUSL_RUSTFLAGS = "-Ctarget-feature=-crt-static"


def _resolve_path_arg(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


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


def _rust_host_triple(env):
    rustc = shutil.which("rustc") or "rustc"
    result = subprocess.run(
        [rustc, "-vV"],
        check=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Unable to determine Rust host triple from rustc -vV")


def _configure_rust_env(env):
    rust_host = _rust_host_triple(env)
    if rust_host.endswith("-musl"):
        existing_rustflags = env.get("RUSTFLAGS", "")
        if MUSL_RUSTFLAGS not in existing_rustflags:
            # AIDEV-NOTE: setuptools-rust forces -crt-static off for musl Python
            # extensions so PyO3 can still emit the _native cdylib. Meson's
            # plain cargo build path needs the same flag for musllinux wheels.
            env["RUSTFLAGS"] = f"{MUSL_RUSTFLAGS} {existing_rustflags}".strip()
            print(
                f"[meson_build_ext] Rust musl host detected; setting RUSTFLAGS={env['RUSTFLAGS']}",
                flush=True,
            )
    return rust_host


def _windows_target_info(ext_suffix: str = "", python_path: str = "") -> tuple[str, str, str]:
    suffix = ext_suffix.lower()
    normalized_python_path = python_path.lower()
    # AIDEV-NOTE: Windows wheel jobs run on 64-bit hosts even for win32 builds.
    # Derive target architecture from the extension tag / target interpreter, not
    # platform.machine(), so Rust/CMake/libddwaf all build for the wheel target.
    if "win32" in suffix or "windows-x86" in normalized_python_path:
        return ("Win32", "i686-pc-windows-msvc", "win32")
    if "win_arm64" in suffix or "windows-arm64" in normalized_python_path:
        return ("ARM64", "aarch64-pc-windows-msvc", "arm64")
    if "win_amd64" in suffix or "windows-x86_64" in normalized_python_path or "windows-amd64" in normalized_python_path:
        return ("x64", "x86_64-pc-windows-msvc", "x64")
    if sys.maxsize <= 2**32:
        return ("Win32", "i686-pc-windows-msvc", "win32")

    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        return ("x64", "x86_64-pc-windows-msvc", "x64")
    if arch in ("arm64", "aarch64"):
        return ("ARM64", "aarch64-pc-windows-msvc", "arm64")
    if arch in ("x86", "i386", "i686"):
        return ("Win32", "i686-pc-windows-msvc", "win32")
    raise RuntimeError(f"Unsupported Windows target architecture: ext_suffix={ext_suffix!r} python={python_path!r}")


# ---------------------------------------------------------------------------
# Rust build
# ---------------------------------------------------------------------------


def cmd_rust(args):
    """Build the Rust (_native) PyO3 extension via cargo."""
    cargo_target_dir = Path(args.cargo_target_dir)
    manifest = Path(args.manifest)
    output = _resolve_path_arg(args.output)
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
    rust_host = _configure_rust_env(env)
    cargo_target = None
    if host == "windows" or CURRENT_OS == "Windows":
        _, cargo_target, _ = _windows_target_info(args.ext_suffix, python)

    # Build with cargo
    cargo_cmd = [
        cargo_bin,
        "build",
        "--release",
        "--manifest-path",
        str(manifest),
    ]
    if cargo_target:
        cargo_cmd += ["--target", cargo_target]
    if features:
        cargo_cmd += ["--features", features]

    run(cargo_cmd, env=env)

    # Locate the built shared library in cargo's release directory
    if cargo_target:
        release_dir = cargo_target_dir / cargo_target / "release"
    else:
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
        print(f"[meson_build_ext] Release dir contents: {list(release_dir.iterdir())}")
        raise RuntimeError(f"Could not find Rust-built _native library in {release_dir}. Tried: {candidates}")

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
            dedup = _ensure_dedup_headers(manifest, Path(args.src_root), rust_host)
            run([dedup, "common.h", "profiling.h"], cwd=str(include_dir))
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


def _libdatadog_tools_rev(manifest: Path) -> str:
    manifest_text = manifest.read_text(encoding="utf-8")
    match = re.search(r'build_common\s*=\s*\{[^}]*\brev\s*=\s*"([^"]+)"', manifest_text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not determine libdatadog tools revision from {manifest}")
    return match.group(1)


def _ensure_dedup_headers(manifest: Path, src_root: Path, rust_host: str) -> str:
    """Install a revision-pinned dedup_headers binary for the active libdatadog toolchain."""
    libdatadog_rev = _libdatadog_tools_rev(manifest)
    # AIDEV-NOTE: dedup_headers is a host executable, not a target artifact. GitLab
    # reuses .download_cache across architectures, so the cache path must include the
    # resolved host triple to avoid reusing an x86_64 binary on aarch64 (or vice versa).
    install_root = src_root / ".download_cache" / "tools" / f"libdatadog-{libdatadog_rev}-{rust_host}"
    dedup = install_root / "bin" / "dedup_headers"
    if dedup.exists():
        return str(dedup)

    print(f"[meson_build_ext] Installing dedup_headers from libdatadog {libdatadog_rev} for {rust_host}...")
    cargo = shutil.which("cargo") or "cargo"
    install_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            cargo,
            "install",
            "--git",
            LIBDATADOG_REPO,
            "--rev",
            libdatadog_rev,
            "--root",
            str(install_root),
            "--bin",
            "dedup_headers",
            "tools",
        ],
        check=True,
    )
    return str(dedup)


# ---------------------------------------------------------------------------
# CMake builds
# ---------------------------------------------------------------------------


def _clean_stale_fetchcontent_state(fetchcontent_dir: Path) -> None:
    current_base_dir = str(fetchcontent_dir)
    for cache_path in fetchcontent_dir.glob("*-subbuild/CMakeCache.txt"):
        try:
            cache_text = cache_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if current_base_dir in cache_text:
            continue

        dep_name = cache_path.parent.name[: -len("-subbuild")]
        # AIDEV-NOTE: CMake FetchContent caches absolute checkout paths in the
        # generated *-build / *-subbuild trees. GitLab reuses .download_cache
        # across different workspace roots, so we must clear only the generated
        # state for a dependency when the cached path no longer matches the
        # current checkout. Keep *-src so the downloaded dependency itself is
        # still reused.
        for suffix in ("-subbuild", "-build"):
            stale_dir = fetchcontent_dir / f"{dep_name}{suffix}"
            if stale_dir.exists():
                print(f"[meson_build_ext] Removing stale FetchContent state: {stale_dir}")
                shutil.rmtree(stale_dir, ignore_errors=True)


def _clean_stale_cmake_build_dir(build_dir: Path, cmake_src_dir: Path) -> None:
    cache_path = build_dir / "CMakeCache.txt"
    if not cache_path.exists():
        return

    try:
        cache_text = cache_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    current_build_dir = str(build_dir)
    current_source_dir = str(cmake_src_dir)
    if current_build_dir in cache_text and current_source_dir in cache_text:
        return

    print(f"[meson_build_ext] Removing stale CMake build dir: {build_dir}")
    shutil.rmtree(build_dir, ignore_errors=True)


def _cmake_parallel_args():
    configured_parallel = os.getenv("CMAKE_BUILD_PARALLEL_LEVEL")
    if configured_parallel:
        try:
            parallel = int(configured_parallel)
        except ValueError:
            print(
                f"[meson_build_ext] WARNING: ignoring invalid CMAKE_BUILD_PARALLEL_LEVEL={configured_parallel!r}",
                flush=True,
            )
        else:
            if parallel > 0:
                # AIDEV-NOTE: GitLab package jobs cap nested CMake concurrency via
                # CMAKE_BUILD_PARALLEL_LEVEL to keep the large IAST C++ build from
                # being OOM-killed. Respect it here instead of forcing host CPU count.
                return ["--parallel", str(parallel)]
            print(
                f"[meson_build_ext] WARNING: ignoring non-positive CMAKE_BUILD_PARALLEL_LEVEL={configured_parallel!r}",
                flush=True,
            )

    return ["--parallel", str(os.cpu_count() or 4)]


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
    output = _resolve_path_arg(args.output)
    install_dir = _resolve_path_arg(args.install_dir)
    component_name = args.component_name
    build_type = args.build_type or "RelWithDebInfo"

    output.parent.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)

    # Build directory alongside source
    build_dir = src_root / ".mesonbuild" / "cmake" / component_name / args.build_key
    _clean_stale_cmake_build_dir(build_dir, cmake_src_dir)
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
    if CURRENT_OS == "Windows":
        cmake_arch, _, _ = _windows_target_info(args.ext_suffix, args.python)
        cmake_args.append(f"-A{cmake_arch}")

    # FetchContent cache for Abseil etc.
    fetchcontent_dir = src_root / ".download_cache" / "_cmake_deps"
    fetchcontent_dir.mkdir(parents=True, exist_ok=True)
    _clean_stale_fetchcontent_state(fetchcontent_dir)
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
    build_args = [cmake, "--build", str(build_dir), "--config", build_type, *_cmake_parallel_args()]
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
    output = _resolve_path_arg(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    build_dir = src_root / ".mesonbuild" / "psutil_build"
    build_dir.mkdir(parents=True, exist_ok=True)

    # Import psutil's setup.py as a module to call get_extensions() without
    # invoking main() (which fails due to missing convert_readme.py in vendor tree).
    spec = importlib.util.spec_from_file_location("ddtrace.vendor.psutil.setup", psutil_dir / "setup.py")
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

    # Prefer the exact interpreter-specific filename first so stale binaries
    # from a different Python version in the source tree are not accidentally
    # copied and merely renamed for the current build.
    exact_name = pattern.rstrip("*") + args.ext_suffix
    exact_candidate = psutil_dir / exact_name
    if exact_candidate.exists():
        candidates = [exact_candidate]
    else:
        # Filter to only shared libraries (exclude .c/.h source files)
        candidates = [
            p
            for p in psutil_dir.glob(pattern)
            if p.suffix in (".so", ".pyd", ".dylib") or p.name.endswith(args.ext_suffix)
        ]
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
    import tarfile
    from urllib.request import urlretrieve

    src_root = Path(args.src_root)
    output = _resolve_path_arg(args.output)
    host = args.host.lower() if args.host else CURRENT_OS.lower()

    LIBDDWAF_VERSION = "1.30.1"
    URL_ROOT = "https://github.com/DataDog/libddwaf/releases/download"
    CACHE_DIR = src_root / ".download_cache"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    machine = platform.machine().lower()

    # Determine OS name for URL
    # AIDEV-NOTE: Keep the cache keyed by the resolved host/arch archive name.
    # Linux and macOS can share the same .download_cache across local + Docker
    # builds, so coarse "already downloaded" checks are not specific enough.
    if host == "darwin":
        os_name = "darwin"
        suffix = ".dylib"
        arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    elif host == "linux":
        os_name = "linux"
        suffix = ".so"
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    elif host == "windows":
        os_name = "windows"
        suffix = ".dll"
        _, _, arch = _windows_target_info(python_path=args.python)
    else:
        print(f"[meson_build_ext] WARNING: unsupported host '{host}', skipping libddwaf download")
        raise RuntimeError(f"Unsupported host for libddwaf download: {host}")

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

    extract_root = CACHE_DIR / "_libddwaf_extract" / f"{host}-{arch}"
    if extract_root.exists():
        shutil.rmtree(extract_root, ignore_errors=True)
    extract_root.mkdir(parents=True, exist_ok=True)

    with tarfile.open(str(cached), "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.name.endswith((suffix, ".so", ".dylib", ".dll"))]
        tar.extractall(members=members, path=str(extract_root))

    candidates = sorted(extract_root.rglob(f"*{suffix}"))
    if not candidates:
        raise RuntimeError(f"Could not find libddwaf shared library in {extract_root}")

    lib_src = None
    for candidate in candidates:
        if candidate.name in {f"libddwaf{suffix}", f"ddwaf{suffix}"}:
            lib_src = candidate
            break
    if lib_src is None:
        lib_src = candidates[0]

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lib_src, output)
    print(f"[meson_build_ext] libddwaf copied: {lib_src} → {output}")


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
    p_cmake.add_argument("--build-key", required=True)
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
    dispatch[args.subcommand](args)


if __name__ == "__main__":
    main()
