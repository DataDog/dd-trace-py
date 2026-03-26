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
import contextlib
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Optional


CURRENT_OS = platform.system()
LIBDATADOG_REPO = "https://github.com/DataDog/libdatadog"
MUSL_RUSTFLAGS = "-Ctarget-feature=-crt-static"
DEFAULT_CI_NESTED_PARALLELISM = 12


def _content_hash(paths: list) -> str:
    """Compute a stable SHA-256 over the contents of a list of files or directory trees.

    AIDEV-NOTE: Used to implement "nothing-changed" fast-exit in meson custom_target
    scripts.  With build_always_stale=true, meson runs these scripts on every
    `meson compile` invocation (= every `pip install -e .` called by riot for each
    test suite).  Without a content hash check, commands like `cargo build` or
    `cmake --build` run every time even when no source file changed, causing
    cumulative slowdowns proportional to the number of riot test suites.

    Only regular files are hashed; directory entries and symlinks are skipped.
    Files are sorted by absolute path for determinism.
    """
    digest = hashlib.sha256()
    collected: list[Path] = []
    for path in paths:
        p = Path(path)
        if p.is_file():
            collected.append(p.resolve())
        elif p.is_dir():
            collected.extend(sorted(f.resolve() for f in p.rglob("*") if f.is_file()))
    for f in sorted(collected):
        digest.update(str(f).encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(f.read_bytes())
        except OSError:
            pass
        digest.update(b"\0")
    return digest.hexdigest()


def _read_stamp(stamp_file: Path) -> str:
    try:
        return stamp_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_content_stamp(stamp_file: Path, content_hash: str) -> None:
    stamp_file.parent.mkdir(parents=True, exist_ok=True)
    stamp_file.write_text(content_hash + "\n", encoding="utf-8")


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


def _parse_parallel_env(var_name: str) -> Optional[int]:
    configured_parallel = os.getenv(var_name)
    if not configured_parallel:
        return None

    try:
        parallel = int(configured_parallel)
    except ValueError:
        print(f"[meson_build_ext] WARNING: ignoring invalid {var_name}={configured_parallel!r}", flush=True)
        return None

    if parallel <= 0:
        print(f"[meson_build_ext] WARNING: ignoring non-positive {var_name}={configured_parallel!r}", flush=True)
        return None

    return parallel


def _nested_build_parallelism(*env_var_names: str) -> int:
    for env_var_name in env_var_names:
        configured_parallel = _parse_parallel_env(env_var_name)
        if configured_parallel is not None:
            return configured_parallel

    if os.getenv("GITLAB_CI") or os.getenv("CI"):
        # AIDEV-NOTE: Some editable/build-isolation code paths in GitLab do not
        # reliably propagate the package job's explicit parallelism env vars into
        # nested helper invocations. Use the same conservative cap as CI config so
        # Cargo/CMake do not fall back to host CPU count and OOM the runner.
        return min(os.cpu_count() or DEFAULT_CI_NESTED_PARALLELISM, DEFAULT_CI_NESTED_PARALLELISM)

    return os.cpu_count() or 4


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

    # AIDEV-NOTE: Fast-exit when Rust source has not changed.
    # riot calls `pip install -e .` (→ meson compile → this script) once per test
    # suite, even when no source changed.  Running `cargo build --release` just to
    # find "nothing to do" takes 3-5 seconds; with dozens of suites this adds up.
    #
    # We hash Cargo.lock (captures all dependency versions) + Cargo.toml (build
    # metadata) + all .rs sources + features string + Python executable path (so
    # a Python version switch forces a rebuild).  If the hash matches the stored
    # stamp and the output exists, we skip cargo entirely.
    src_dir = manifest.parent / "src"
    rust_hash_inputs = [manifest, manifest.parent / "Cargo.lock"]
    if src_dir.is_dir():
        rust_hash_inputs.append(src_dir)
    rust_source_hash = _content_hash(rust_hash_inputs) + ":" + (features or "") + ":" + python
    rust_stamp_file = output.parent / (output.name + ".rust_src.stamp")
    if output.exists() and _read_stamp(rust_stamp_file) == rust_source_hash:
        print(f"[meson_build_ext] Rust output up-to-date, skipping cargo build ({output.name})", flush=True)
        _write_stamp(args)
        return

    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(cargo_target_dir)
    env["PYO3_PYTHON"] = python
    # Prefer stable toolchain to avoid nightly/MSRV mismatches
    if not env.get("RUSTUP_TOOLCHAIN"):
        env["RUSTUP_TOOLCHAIN"] = "stable"
    if "CARGO_BUILD_JOBS" not in env:
        env["CARGO_BUILD_JOBS"] = str(_nested_build_parallelism("CARGO_BUILD_JOBS", "CMAKE_BUILD_PARALLEL_LEVEL"))
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

    # Record the source hash so future calls can fast-exit if nothing changed.
    _write_content_stamp(rust_stamp_file, rust_source_hash)
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


@contextlib.contextmanager
def _fetchcontent_configure_lock(fetchcontent_dir: Path):
    """Serialize cmake configure runs that share the same FetchContent base dir.

    AIDEV-NOTE: Each component now uses its own FETCHCONTENT_BASE_DIR
    (component_name subdirectory), so concurrent cmake --build invocations no
    longer share an absl-build/ directory and the build-phase race is avoided.
    This lock still guards the configure phase in case two build processes for
    the *same* component run simultaneously (e.g. parallel pip installs).  It
    serialises cmake configure + FetchContent populate on a per-base-dir flock
    so concurrent git operations on *-src/.git do not race.
    Windows does not have fcntl; the lock is silently skipped there.
    """
    lock_path = fetchcontent_dir / ".cmake_configure.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        lf = lock_path.open("a")
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            lf.close()
    except ImportError:
        yield  # Windows: no fcntl, proceed without locking


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
    parallel = _nested_build_parallelism("CMAKE_BUILD_PARALLEL_LEVEL", "CARGO_BUILD_JOBS")
    return ["--parallel", str(parallel)]


def _cmake_configure_hash(cmake_args: list, build_dir: Path) -> str:
    """Compute a stable hash over cmake configure arguments.

    AIDEV-NOTE: Covers all -D/-S/-B/-A flags so any change to Python
    interpreter, build type, install prefix, or directory layout invalidates
    the cache and forces a re-configure.  Used by cmd_cmake to skip the
    expensive cmake -S/-B/-D step on incremental builds.

    AIDEV-NOTE: The cmake binary itself is included in the hash because pip
    installs cmake into an ephemeral build env whose path changes on every
    invocation (e.g. pip-build-env-abc123).  CMakeCache.txt bakes in that
    path; if a second pip install -e . skips configure but cmake --build
    tries to exec the old (now-deleted) cmake binary via the cached Makefile
    rules, the build fails with "No such file or directory".  Including the
    cmake binary path ensures a new ephemeral env triggers a fresh configure.
    """
    relevant = sorted(
        arg
        for arg in cmake_args
        if isinstance(arg, str)
        and (arg.startswith("-D") or arg.startswith("-S") or arg.startswith("-B") or arg.startswith("-A"))
    )
    relevant.append(str(build_dir))
    # First element of cmake_args is the cmake binary itself
    cmake_binary = str(cmake_args[0]) if cmake_args else ""
    digest = hashlib.sha256()
    digest.update(cmake_binary.encode("utf-8"))
    digest.update(b"\0")
    for item in relevant:
        digest.update(item.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


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
    # AIDEV-NOTE: Each component gets its own FetchContent base dir to avoid
    # cmake --build races.  iast_native, memalloc, and stack all use FetchContent
    # for abseil; if they shared a single base dir their concurrent cmake --build
    # invocations would write to the same absl-build/ directory and race on
    # ranlib/ar operations.  Using component_name as a subdirectory gives each
    # component its own absl-build/ tree.  The configure-phase lock
    # (_fetchcontent_configure_lock) is no longer needed since there is no shared
    # git state, but it is kept for safety on the configure path.
    fetchcontent_dir = src_root / ".download_cache" / "_cmake_deps" / component_name
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

    # AIDEV-NOTE: Configure-hash caching: skip the expensive cmake -S/-B/-D
    # configure step when the build dir already has a valid CMakeCache.txt and
    # cmake arguments haven't changed.  Always run cmake --build (fast ~0.5s
    # when no C++ files changed); cmake's own incremental logic avoids
    # recompiling unchanged sources.  Combined with build_always_stale: true
    # on the meson custom_target, this gives correct incremental C++ builds
    # without listing every source file as a meson dependency.
    #
    # The _fetchcontent_configure_lock serialises cmake configure across
    # components that share FETCHCONTENT_BASE_DIR (e.g. iast_native and
    # memalloc both use abseil).  Without the lock, a concurrent re-configure
    # triggered by a cmake binary change races on git operations inside
    # the shared *-src/.git directory.
    cmake_cache = build_dir / "CMakeCache.txt"
    hash_file = build_dir / ".meson_cmake_args_hash"
    current_hash = _cmake_configure_hash(cmake_args, build_dir)

    # AIDEV-NOTE: Fast-exit for cmake --build when configure args and C++ source
    # have not changed.  riot calls `pip install -e .` (→ meson compile → this
    # script) for every test suite run.  cmake --build takes ~0.5s per component
    # even when nothing changed; across 4 cmake components and dozens of suites
    # this accumulates.
    #
    # Strategy: store a content hash of all C/C++/CMake source files alongside
    # the configure-args hash.  When both match and the output exists, skip
    # cmake configure + build + install entirely.
    cmake_src_hash = _content_hash([cmake_src_dir])
    cmake_src_stamp_file = build_dir / ".meson_cmake_src_hash"
    configure_matches = cmake_cache.exists() and hash_file.exists() and _read_stamp(hash_file) == current_hash
    source_matches = cmake_src_stamp_file.exists() and _read_stamp(cmake_src_stamp_file) == cmake_src_hash
    if configure_matches and source_matches and output.exists():
        print(
            f"[meson_build_ext] cmake output up-to-date, skipping configure+build ({component_name})",
            flush=True,
        )
        _write_stamp(args)
        return

    with _fetchcontent_configure_lock(fetchcontent_dir):
        _clean_stale_fetchcontent_state(fetchcontent_dir)
        if configure_matches:
            print(
                f"[meson_build_ext] cmake configure up-to-date, skipping ({component_name})",
                flush=True,
            )
        else:
            run(cmake_args)
            hash_file.write_text(current_hash + "\n", encoding="utf-8")

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

    # Record the source hash so future calls can fast-exit if nothing changed.
    _write_content_stamp(cmake_src_stamp_file, cmake_src_hash)
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

    # AIDEV-NOTE: Fast-exit for psutil when nothing changed.
    # psutil is a vendored, pinned dependency whose source never changes between
    # runs.  Rebuilding it on every `pip install -e .` (= every riot suite) wastes
    # ~2s per suite.  Hash the psutil source tree; if it matches the stored stamp
    # and the output exists, skip the rebuild entirely.
    psutil_src_hash = _content_hash([psutil_dir]) + ":" + args.ext_suffix
    psutil_stamp_file = output.parent / (output.name + ".psutil_src.stamp")
    if output.exists() and _read_stamp(psutil_stamp_file) == psutil_src_hash:
        print("[meson_build_ext] psutil output up-to-date, skipping build", flush=True)
        _write_stamp(args)
        return

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
    # Record the source hash so future calls can fast-exit if nothing changed.
    _write_content_stamp(psutil_stamp_file, psutil_src_hash)
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

    # AIDEV-NOTE: asm.py (ddtrace/internal/settings/asm.py) uses os.path.dirname(__file__)
    # to build a raw filesystem path to libddwaf and passes it to ctypes.CDLL().  That
    # code predates meson and expects the library at:
    #   <src_root>/ddtrace/appsec/_ddwaf/libddwaf/<arch>/lib/libddwaf.<ext>
    # The arch key used by asm.py is the raw platform.machine().lower() result (or
    # translated via TRANSLATE_ARCH: amd64→x64, i686→x86_64, x86→win32).
    # We mirror the file there so asm.py continues to work without modification.
    TRANSLATE_ARCH = {"amd64": "x64", "i686": "x86_64", "x86": "win32"}
    asm_arch = TRANSLATE_ARCH.get(arch, arch)
    source_tree_lib = src_root / "ddtrace" / "appsec" / "_ddwaf" / "libddwaf" / asm_arch / "lib" / lib_src.name
    source_tree_lib.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lib_src, source_tree_lib)
    print(f"[meson_build_ext] libddwaf source-tree copy: {lib_src} → {source_tree_lib}")


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
