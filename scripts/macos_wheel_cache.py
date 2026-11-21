"""Publish or restore the complete cache for the macOS amd64 CPython 3.12 wheel."""

import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

import ext_cache
from extract_debug_symbols import dsym_matches


class CacheMiss(Exception):
    """A cache was unavailable or incompatible."""


class UnsafeRestore(Exception):
    """A failed restore could not be completely rolled back."""


def compatibility_key() -> bytes:
    # Hash tracked build inputs and require an unmodified checkout. Regular Python
    # modules installed by the wheel can vary across revisions.
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=ext_cache.ROOT, check=True)
    tracked = subprocess.check_output(
        ["git", "ls-files", "--stage", "-z", "--", ".", ":(exclude)docs/**", ":(exclude)releasenotes/**"],
        cwd=ext_cache.ROOT,
    )
    digest = hashlib.sha256()
    for record in tracked.split(b"\0"):
        metadata, _, path = record.partition(b"\t")
        if metadata.startswith((b"100644 ", b"100755 ")) and path.startswith((b"ddtrace/", b"tests/")):
            if path.endswith(b".py"):
                continue
        digest.update(record + b"\0")

    tool_versions = [
        subprocess.check_output(command, text=True).strip()
        for command in (
            ["sw_vers", "-buildVersion"],
            ["xcrun", "--sdk", "macosx", "--show-sdk-version"],
            ["clang", "--version"],
            ["rustc", "-vV"],
            ["cargo", "--version"],
            ["uv", "--version"],
        )
    ]
    flags = (
        "ARCHFLAGS",
        "CC",
        "CXX",
        "CFLAGS",
        "CPPFLAGS",
        "CXXFLAGS",
        "LDFLAGS",
        "SDKROOT",
        "DEVELOPER_DIR",
        "MACOSX_DEPLOYMENT_TARGET",
        "RUSTFLAGS",
        "CARGO_ENCODED_RUSTFLAGS",
        "RUSTC",
        "RUSTC_WRAPPER",
        "RUSTC_WORKSPACE_WRAPPER",
        "CARGO_BUILD_TARGET",
        "CIBW_BUILD",
        "PLAT",
        "DD_COMPILE_MODE",
        "DD_COMPILE_DEBUG",
        "DD_CARGO_ARGS",
        "DD_COMPILE_ABSEIL",
        "DD_FAST_BUILD",
        "DD_CYTHONIZE",
        "DD_SETUP_FORCE_CYTHONIZE",
        "DD_SERVERLESS_BUILD",
        "DD_PROFILING_NATIVE_HEAP_ENABLED",
        "DD_PROFILING_NATIVE_HEAP_TEST_SUPPORT",
        "DD_CMAKE_INCREMENTAL_BUILD",
        "_DD_NATIVE_CARGO_TARGET_DIR",
    )
    env = {
        name: value
        for name, value in os.environ.items()
        if name in flags or name.startswith(("CMAKE_", "CARGO_PROFILE_", "CARGO_TARGET_", "PYO3_"))
    }
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    inputs = {
        "tools": tool_versions,
        "python": [sys.version, platform.machine(), sysconfig.get_config_var("SOABI")],
        "dependencies": {name: version(name) for name in ("setuptools", "Cython", "setuptools-rust", "cmake")},
        "flags": env,
        "cargo_config": [
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (cargo_home / "config", cargo_home / "config.toml")
            if path.is_file()
        ],
    }
    digest.update(json.dumps(inputs, sort_keys=True).encode())
    return digest.hexdigest().encode()


def wheel_inputs() -> list[tuple[str, Path, Path | None]]:
    ext_cache.INPLACE = False
    entries, _ = ext_cache.invoke_ext_hashes()
    inputs = []
    for name, source_hash, destination in entries:
        target = Path(destination)
        bundle: Path | None
        if target.suffix == ".so":
            build_lib, *parts = target.relative_to(ext_cache.ROOT / "build").parts
            if not build_lib.startswith("lib.") or not parts or parts[0] != "ddtrace":
                raise CacheMiss(f"Unexpected wheel library path for {name}")
            bundle = Path(*parts).with_suffix(".dSYM")
        elif target == ext_cache.ROOT / "src/native/target3.12/include/datadog" / target.name and target.suffix == ".h":
            bundle = None
        else:
            raise CacheMiss(f"Unsupported cache entry for {name}")

        if target.resolve() != target:
            raise CacheMiss(f"Unexpected resolved build path for {name}")
        inputs.append((f"extensions/{name}/{source_hash}/{target.name}", target, bundle))

    if not any(bundle is not None for _, _, bundle in inputs):
        raise CacheMiss("No compiled extensions found")
    return inputs


def save(archive_path: Path, symbols_zip: Path) -> None:
    key = compatibility_key()
    inputs = wheel_inputs()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(symbols_zip) as symbols:
        symbols_dir = Path(tmp)
        symbols.extractall(symbols_dir)
        for _, target, bundle in inputs:
            if bundle is not None and not dsym_matches(target, symbols_dir / bundle):
                raise CacheMiss(f"Missing or mismatched dSYM for {target.name}")

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("build-key", key)
        archive.write(symbols_zip, "symbols.zip", compress_type=zipfile.ZIP_STORED)
        for member, target, _ in inputs:
            archive.write(target, member)


def restore(archive_path: Path, staging: Path) -> None:
    # Validate the complete archive before changing any build outputs.
    files = []
    with zipfile.ZipFile(archive_path) as archive:
        if archive.read("build-key") != compatibility_key():
            raise CacheMiss("Build inputs or toolchain differ from main")
        symbols_zip = archive.extract("symbols.zip", staging)
        with zipfile.ZipFile(symbols_zip) as symbols:
            symbols.extractall(staging / "symbols")
        for member, target, bundle in wheel_inputs():
            if target.exists() or target.is_symlink():
                raise CacheMiss(f"Build output already exists for {target.name}")
            cached = Path(archive.extract(member, staging))
            if bundle is not None and not dsym_matches(cached, staging / "symbols" / bundle):
                raise CacheMiss(f"Missing or mismatched dSYM for {target.name}")
            files.append((cached, target))

    # If the restore process is interrupted, the caller must not build from partial outputs.
    installing = staging / "installing"
    installing.touch()
    installed = []
    try:
        for cached, target in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            installed.append(target)
            shutil.copyfile(cached, target)
    except OSError:
        try:
            for target in installed:
                target.unlink(missing_ok=True)
            installing.unlink()
        except OSError as exc:
            raise UnsafeRestore("Unable to remove a partially restored build") from exc
        raise
    installing.unlink()
    print(f"macOS wheel cache: hit, restored {len(files)} files with matching symbols for all extensions")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["save", "restore"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("symbols_or_staging", type=Path)
    args = parser.parse_args()

    command = save if args.command == "save" else restore
    try:
        command(args.archive, args.symbols_or_staging)
    except UnsafeRestore as exc:
        print(f"macOS wheel cache: {exc}", file=sys.stderr)
        return 3
    except (CacheMiss, KeyError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"macOS wheel cache: {args.command} skipped ({exc})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
