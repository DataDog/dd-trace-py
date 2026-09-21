"""Tests for the microbenchmark runner's CPU assignment.

Run directly: python benchmarks/base/test_run.py

These live next to run.py rather than under tests/ because run.py is CI harness
code for the benchmarking platform, not part of the shipped ddtrace package.
"""

import sys

from run import assign_configs_to_cpu_groups
from run import cpu_affinity_to_cpu_groups


def test_cpu_affinity_parses_ranges_and_singles():
    assert cpu_affinity_to_cpu_groups("6-11", 1) == [[6], [7], [8], [9], [10], [11]]
    assert cpu_affinity_to_cpu_groups("24-27", 2) == [[24, 25], [26, 27]]
    assert cpu_affinity_to_cpu_groups("6-9,14,15", 2) == [[6, 7], [8, 9], [14, 15]]


def test_cpu_affinity_rejects_indivisible_core_count():
    try:
        cpu_affinity_to_cpu_groups("24-46", 2)
    except ValueError:
        return
    raise AssertionError("expected ValueError for 23 cores with CPUS_PER_RUN=2")


def test_assignment_is_identical_across_invocations():
    # The candidate and the baseline are separate run.py processes. Both must map
    # every config to the same core group or the per-core offset stops cancelling.
    config = {"c{}".format(i): {"n": i} for i in range(7)}
    groups = cpu_affinity_to_cpu_groups("24-47", 2)

    first = assign_configs_to_cpu_groups(config, groups)
    second = assign_configs_to_cpu_groups(config, groups)

    assert first == second


def test_assignment_does_not_depend_on_config_key_order():
    forward = {"alpha": {}, "beta": {}, "gamma": {}, "delta": {}}
    reversed_order = {k: forward[k] for k in reversed(list(forward))}
    groups = cpu_affinity_to_cpu_groups("24-27", 1)

    a = {cname: cpus for cname, _, cpus in assign_configs_to_cpu_groups(forward, groups)}
    b = {cname: cpus for cname, _, cpus in assign_configs_to_cpu_groups(reversed_order, groups)}

    assert a == b
    assert a == {"alpha": [24], "beta": [25], "delta": [26], "gamma": [27]}


def test_assignment_wraps_when_configs_exceed_groups():
    config = {"c{}".format(i): {} for i in range(5)}
    groups = cpu_affinity_to_cpu_groups("24-25", 1)

    assigned = assign_configs_to_cpu_groups(config, groups)

    assert [cpus for _, _, cpus in assigned] == [[24], [25], [24], [25], [24]]


def test_assignment_covers_every_config_exactly_once():
    config = {"c{}".format(i): {"n": i} for i in range(13)}
    groups = cpu_affinity_to_cpu_groups("24-47", 2)

    assigned = assign_configs_to_cpu_groups(config, groups)

    assert sorted(cname for cname, _, _ in assigned) == sorted(config)
    for cname, cvars, _ in assigned:
        assert cvars is config[cname]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
        except Exception as exc:
            failures += 1
            print("FAIL {}: {!r}".format(name, exc))
        else:
            print("ok   {}".format(name))
    sys.exit(1 if failures else 0)
