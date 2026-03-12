"""Analyze and compare pprof output from the lock profiler demo.

Usage:
    python analyze.py <main_prefix> <main_pid> <fix_prefix> <fix_pid>
"""

import glob
import sys

import zstandard as zstd

from tests.profiling.collector.pprof_utils import get_samples_with_value_type
from tests.profiling.collector.pprof_utils import pprof_pb2


def parse_newest_profile(prefix: str) -> tuple[str, "pprof_pb2.Profile"]:
    files = sorted(glob.glob(prefix + ".*.pprof"), key=lambda f: int(f.rsplit(".", 2)[-2]))
    if not files:
        return "(none)", pprof_pb2.Profile()

    filename = files[-1]
    with open(filename, "rb") as fp:
        data = zstd.ZstdDecompressor().stream_reader(fp).read()
    profile = pprof_pb2.Profile()
    profile.ParseFromString(data)

    if len(profile.sample) == 0 and len(files) > 1:
        filename = files[-2]
        with open(filename, "rb") as fp:
            data = zstd.ZstdDecompressor().stream_reader(fp).read()
        profile = pprof_pb2.Profile()
        profile.ParseFromString(data)

    return filename, profile


def count_lock_samples(profile: "pprof_pb2.Profile") -> tuple[int, int, int]:
    total = len(profile.sample)
    try:
        acquire = len(get_samples_with_value_type(profile, "lock-acquire"))
    except StopIteration:
        acquire = 0
    try:
        release = len(get_samples_with_value_type(profile, "lock-release"))
    except StopIteration:
        release = 0
    return total, acquire, release


def main() -> int:
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <main_prefix> <main_pid> <fix_prefix> <fix_pid>", file=sys.stderr)
        return 2

    main_file, main_profile = parse_newest_profile(f"{sys.argv[1]}.{sys.argv[2]}")
    fix_file, fix_profile = parse_newest_profile(f"{sys.argv[3]}.{sys.argv[4]}")

    main_total, main_acq, main_rel = count_lock_samples(main_profile)
    fix_total, fix_acq, fix_rel = count_lock_samples(fix_profile)

    print()
    print(f"{'':30s} {'main':>10s}   {'fix':>10s}")
    print(f"{'':30s} {'----':>10s}   {'---':>10s}")
    print(f"{'total samples':30s} {main_total:>10d}   {fix_total:>10d}")
    print(f"{'lock-acquire samples':30s} {main_acq:>10d}   {fix_acq:>10d}")
    print(f"{'lock-release samples':30s} {main_rel:>10d}   {fix_rel:>10d}")
    print()

    if main_acq == 0 and fix_acq > 0:
        print("RESULT: As expected -- main has 0 lock samples, fix branch has lock samples.")
        return 0
    elif main_acq > 0:
        print("RESULT: Unexpected -- main already has lock samples (bug may be fixed on main).")
        return 0
    else:
        print("RESULT: FAIL -- fix branch also has 0 lock samples (fix not working).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
