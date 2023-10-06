#!/usr/bin/env python3
"""
Script to download or build all required wheels (including dependencies) of the ddtrace
Python package for relevant Python versions (+ abis), C library platforms and
architectures and unpack them into Python-specific site-packages directories.

These site-package directories provide a portable installation of ddtrace which can be
used on multiple platforms and architectures.

Currently, the only OS supported is Linux.

This script has been tested with pip 21.0.0 and is confirmed to not work with
20.0.2.

Usage:
        ./build.py --help

"""
import argparse
import os
import subprocess
import sys


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--ddtrace-version", type=str, required=True)
parser.add_argument("--output-dir", type=str, required=True)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--verbose", action="store_true")
args = parser.parse_args()

dl_dir = args.output_dir
print("Saving wheels to %s" % dl_dir)

# We want to make sure to pull the binary wheel for ddtrace and
# otherwise build wheels for dependencies
cmd = [
    sys.executable,
    "-m",
    "pip",
    "wheel",
    "ddtrace==%s" % args.ddtrace_version,
    "--only-binary=ddtrace",
    "--wheel-dir",
    dl_dir,
]
if args.verbose:
    print(" ".join(cmd))

if not args.dry_run:
    subprocess.run(cmd, capture_output=not args.verbose, check=True)

    wheel_files = [f for f in os.listdir(dl_dir) if f.endswith(".whl")]
    for whl in wheel_files:
        wheel_file = os.path.join(dl_dir, whl)
        print("Unpacking %s" % wheel_file)
        # -q for quieter output, else we get all the files being unzipped.
        subprocess.run(
            [
                "unzip",
                "-q",
                "-o",
                wheel_file,
                "-d",
                dl_dir,
            ]
        )
        # Remove the wheel as it has been unpacked
        os.remove(wheel_file)
