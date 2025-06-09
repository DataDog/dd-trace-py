import argparse
import os
import subprocess
import sys
from pathlib import Path

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


def build_crate(crate_dir: Path, release: bool, features: list = None):
    env = os.environ.copy()
    abs_dir = crate_dir.absolute()
    env["CARGO_TARGET_DIR"] = str(abs_dir / "target")

    build_cmd = ["cargo", "build"]

    if release:
        build_cmd.append("--release")

    if features:
        cmd_features = ', '.join(features)
        build_cmd += ["--features", cmd_features]

    subprocess.run(
            build_cmd,
            cwd=str(crate_dir),
            check=True,
            env=env,
            )

    if 'profiling' in features:
        install_dedup_headers()

        subprocess.run(
            ["dedup_headers", "common.h", "crashtracker.h", "profiling.h"],
            cwd=str(abs_dir / "target" / "include" / "datadog"),
            check=True,
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
        parser.add_argument("--features", nargs='*', help="List of features")

        args = parser.parse_args()

        build_crate(Path(args.crate), args.release, args.features)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
