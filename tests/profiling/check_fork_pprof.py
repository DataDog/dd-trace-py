"""
Helper for the fork+gthread CI regression workflow.

Usage:
    python check_fork_pprof.py <repo_root> <pprof_prefix> <child_pid>

Exits 0 if worker threads appear in wall-time samples, 1 if only
MainThread is visible (bug present), 2 if no pprof file was found.
"""

import glob
import sys


sys.path.append(sys.argv[1])  # repo root appended so venv's compiled ddtrace takes priority
from tests.profiling.collector import pprof_utils  # noqa: E402


prefix = sys.argv[2]
child_pid = sys.argv[3]
full_prefix = f"{prefix}.{child_pid}"

files = glob.glob(f"{full_prefix}.*pprof")
if not files:
    print(f"NO_PPROF child={child_pid}")
    sys.exit(2)

profile = pprof_utils.parse_newest_profile(full_prefix, allow_penultimate=True)
wt = pprof_utils.get_samples_with_value_type(profile, "wall-time")

names = set()
for s in wt:
    lbl = pprof_utils.get_label_with_key(profile.string_table, s, "thread name")
    if lbl:
        names.add(profile.string_table[lbl.str])

workers = {n for n in names if n != "MainThread"}
print(f"samples={len(wt)} threads={sorted(names)}")

if not workers:
    print("BUG_PRESENT")
    sys.exit(1)

print("OK")
