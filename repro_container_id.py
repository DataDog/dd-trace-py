"""A/B repro for the runtime-metrics ``pod_name`` gap on cgroup v2 hosts.

Background
----------
``runtime.python.*`` metrics are shipped over DogStatsD. The Datadog Agent only
attaches orchestrator tags such as ``pod_name`` via *origin detection*, which
requires the tracer to send a container/origin field (``|c:<id>``) with each
metric. That origin is produced by ``ddtrace/vendor/dogstatsd/container.py``.

The pre-fix reader only parsed ``/proc/self/cgroup``. On cgroup v2 nodes that
file is just ``0::/`` (no parseable container ID), so the reader returned
``None`` -> no origin was sent -> the Agent could not attach ``pod_name``. The
fix adds the cgroup v2 fallback (cgroup controller inode -> ``in-<inode>``) and
the standard ``ci-<id>`` prefix for cgroup v1.

Usage
-----
    # B (this branch, fixed):
    python3 repro_container_id.py

    # A (main / pre-fix), pointing at any other container.py:
    python3 repro_container_id.py /tmp/container_main.py

The script is version-agnostic: it works against both the pre-fix and post-fix
``container.py`` so the same harness can be used for an A/B comparison.
"""

import importlib.util
import os
import sys
import tempfile
from types import ModuleType
from typing import Any
from typing import Optional


# Representative /proc/self/cgroup contents.
CGROUP_V1_DOCKER = (
    "11:memory:/docker/"
    "3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860\n"
    "10:cpu,cpuacct:/docker/"
    "3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860\n"
)
# cgroup v2: a single unified hierarchy, no container id in the path.
CGROUP_V2 = "0::/\n"


def load_container_module(path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_vendored_container", path)
    if spec is None or spec.loader is None:
        raise ImportError("Could not load container module from {0}".format(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_cgroup(content: str) -> str:
    fp = tempfile.NamedTemporaryFile(mode="w", suffix=".cgroup", delete=False)
    fp.write(content)
    fp.close()
    return fp.name


def resolve_origin(ContainerID: Any, cgroup_content: str) -> tuple[Optional[str], str]:
    """Resolve the origin a pod would send, given a /proc/self/cgroup content.

    Mirrors how ``ContainerID.__init__`` chooses a strategy, but feeds fixtures
    instead of reading the host's real cgroup files so it runs anywhere.
    Returns the origin string (or None) and the strategy used.
    """
    reader = ContainerID.__new__(ContainerID)
    cgroup_path = write_cgroup(cgroup_content)
    reader.CGROUP_PATH = cgroup_path
    try:
        # Post-fix path read (cgroup v1) -> "ci-<id>".
        if hasattr(reader, "_read_cgroup_path"):
            ci = reader._read_cgroup_path()
            if ci:
                return ci, "cgroup-path (ci-)"
        # Post-fix inode fallback (cgroup v2) -> "in-<inode>".
        if hasattr(reader, "_get_cgroup_from_inode"):
            mount_dir = tempfile.mkdtemp()
            reader.CGROUP_MOUNT_PATH = mount_dir  # stand in for /sys/fs/cgroup
            try:
                in_inode = reader._get_cgroup_from_inode()
            finally:
                os.rmdir(mount_dir)
            if in_inode:
                return in_inode, "cgroup-inode (in-)"
        # Pre-fix behaviour: only the raw path parse exists.
        raw = reader._read_container_id(cgroup_path)
        return raw, "raw cgroup parse"
    finally:
        os.unlink(cgroup_path)


def main() -> int:
    container_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "ddtrace",
            "vendor",
            "dogstatsd",
            "container.py",
        )
    )
    mod = load_container_module(container_path)
    ContainerID = mod.ContainerID

    has_v2_fallback = hasattr(ContainerID, "_get_cgroup_from_inode")
    variant = "B: fixed (cgroup v2 fallback present)" if has_v2_fallback else "A: pre-fix (no cgroup v2 fallback)"

    v1, v1_how = resolve_origin(ContainerID, CGROUP_V1_DOCKER)
    v2, v2_how = resolve_origin(ContainerID, CGROUP_V2)

    print("container.py: {0}".format(container_path))
    print("variant:      {0}".format(variant))
    print("{0:<14} origin={1!s:<70} sent={2:<5} via {3}".format("cgroup v1:", v1, bool(v1), v1_how))
    print("{0:<14} origin={1!s:<70} sent={2:<5} via {3}".format("cgroup v2:", v2, bool(v2), v2_how))

    # Exit non-zero if cgroup v2 produces no origin (the bug).
    return 0 if v2 else 1


if __name__ == "__main__":
    sys.exit(main())
