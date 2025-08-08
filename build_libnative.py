import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import List
from typing import Optional


def is_installed(bin_file):
    for path in os.environ.get("PATH", "").split(os.pathsep):
        if os.path.isfile(os.path.join(path, bin_file)):
            return True

    return False


def install_dedup_headers():
    if not is_installed("dedup_headers"):
        subprocess.run(
            ["cargo", "install", "--git", "https://github.com/DataDog/libdatadog", "--bin", "dedup_headers", "tools"],
            check=True,
        )


def build_crate(crate_dir: Path, release: bool, features: Optional[List[str]] = None):
    env = os.environ.copy()
    abs_dir = crate_dir.absolute()
    target_dir = abs_dir / "target"
    env["CARGO_TARGET_DIR"] = str(target_dir)

    build_cmd = ["cargo", "build"]

    if release:
        build_cmd.append("--release")

    if features:
        cmd_features = ", ".join(features)
        build_cmd += ["--features", cmd_features]

    # Check if we're on Windows and if Python is 32-bit
    if platform.system() == "Windows" and sys.maxsize <= 2**32:
        # We're building for 32-bit Windows, use the i686 target
        build_cmd += ["--target", "i686-pc-windows-msvc"]

    subprocess.run(
        build_cmd,
        cwd=str(crate_dir),
        check=True,
        env=env,
    )

    if features and "profiling" in features:
        install_dedup_headers()

        # Add cargo binary folder to PATH
        home = os.path.expanduser("~")
        cargo_bin = os.path.join(home, ".cargo", "bin")
        dedup_env = os.environ.copy()
        dedup_env["PATH"] = cargo_bin + os.pathsep + env["PATH"]

        subprocess.run(
            ["dedup_headers", "common.h", "profiling.h"],
            cwd=str(target_dir / "include" / "datadog"),
            check=True,
            env=dedup_env,
        )


def clean_crate(crate_dir: Path):
    target_dir = crate_dir.absolute() / "target"
    if target_dir.exists():
        subprocess.run(
            ["cargo", "clean"],
            cwd=str(crate_dir),
            check=True,
        )


def main():
    try:
        parser = argparse.ArgumentParser(description="Build Rust module")
        parser.add_argument("--crate", required=True, help="Rust crate location")
        parser.add_argument("--release", action="store_true", help="Release profile")
        parser.add_argument("--features", nargs="*", help="List of features")

        args = parser.parse_args()

        build_crate(Path(args.crate), args.release, args.features)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
