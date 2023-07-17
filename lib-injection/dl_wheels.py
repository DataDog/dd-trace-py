#!/usr/bin/env python3
"""
Script to download all required wheels (including dependencies) of the ddtrace
Python package for relevant Python versions (+ abis), C library platforms and
architectures and merge them into a "megawheel" directory.

This directory provides a portable installation of ddtrace which can be used
on multiple platforms and architectures.

Currently the only OS supported is Linux.

This script has been tested with 21.0.0 and is confirmed to not work with
20.0.2.

Usage:
        ./dl_wheels.py --help


The downloaded wheels can then be installed locally using:
        pip install --no-index --find-links <dir_of_downloaded_wheels> ddtrace
"""
import argparse
import itertools
import subprocess
import sys

import packaging.version


# Do a check on the pip version since older versions are known to be
# incompatible.
MIN_PIP_VERSION = packaging.version.parse("21.0")
cmd = [sys.executable, "-m", "pip", "--version"]
res = subprocess.run(cmd, capture_output=True)
out = res.stdout.decode().split(" ")[1]
pip_version = packaging.version.parse(out)
if pip_version < MIN_PIP_VERSION:
    print(
        "WARNING: using known incompatible version, %r, of pip. The minimum compatible pip version is %r"
        % (pip_version, MIN_PIP_VERSION)
    )


supported_pythons = ["2.7", "3.6", "3.7", "3.8", "3.9", "3.10", "3.11"]
supported_arches = ["aarch64", "x86_64", "i686"]
supported_platforms = ["musllinux_1_1", "manylinux2014"]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--python-version",
    choices=supported_pythons,
    action="append",
    required=True,
)
parser.add_argument(
    "--arch",
    choices=supported_arches,
    action="append",
    required=True,
)
parser.add_argument(
    "--platform",
    choices=supported_platforms,
    action="append",
    required=True,
)
parser.add_argument("--ddtrace-version", type=str, required=True)
parser.add_argument("--output-dir", type=str, required=True)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--verbose", action="store_true")
args = parser.parse_args()

dl_dir = args.output_dir
print("saving wheels to %s" % dl_dir)

for python_version, arch, platform in itertools.product(args.python_version, args.arch, args.platform):
    print("Downloading %s %s %s wheel" % (python_version, arch, platform))
    abi = "cp%s" % python_version.replace(".", "")
    # Have to special-case these versions of Python for some reason.
    if python_version in ["2.7", "3.5", "3.6", "3.7"]:
        abi += "m"

    # See the docs for an explanation of all the options used:
    # https://pip.pypa.io/en/stable/cli/pip_download/
    #   only-binary=:all: is specified to ensure we get all the dependencies of ddtrace as well.
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "ddtrace==%s" % args.ddtrace_version,
        "--platform",
        "%s_%s" % (platform, arch),
        "--python-version",
        python_version,
        "--abi",
        abi,
        "--only-binary=:all:",
        "--dest",
        dl_dir,
    ]
    if args.verbose:
        print(" ".join(cmd))

    if not args.dry_run:
        subprocess.run(cmd, capture_output=not args.verbose, check=True)
