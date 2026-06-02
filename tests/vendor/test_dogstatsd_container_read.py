"""Tests for the pre-existing ContainerID._read_container_id helper.

This is a stacked PR on top of the cgroup v2 origin-detection fix. The helper
was present before that change but had zero test coverage. It is the shared
low-level parser used by both _read_cgroup_path (cgroup v1) and directly by
the old __init__ implementation, and is now also called internally from the
new _read_cgroup_path path.

Cases covered:
  - Docker cgroup v1 path
  - Kubernetes cgroup v1 path (pod + container id)
  - ECS classic path
  - Fargate >= 1.4.0 (UUID task ID format)
  - k8s with scoped slice path (docker-<hex>.scope)
  - cgroup v2 unified (0::/) -> None
  - Non-containerised Linux session -> None
  - Empty file -> None
  - File not found (ENOENT) -> None (suppressed)
  - Non-ENOENT IOError -> raises NotImplementedError
  - Unexpected exception -> raises UnresolvableContainerID
"""

import errno
import os
import tempfile
from typing import Optional
from unittest import mock

from ddtrace.vendor.dogstatsd.container import ContainerID
from ddtrace.vendor.dogstatsd.container import UnresolvableContainerID


# ---------------------------------------------------------------------------
# Representative /proc/self/cgroup fixtures
# ---------------------------------------------------------------------------

CONTAINER_ID_64 = "3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860"
CONTAINER_ID_K8S = "3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1"
TASK_ID_FARGATE = "34dc0b5e626f2c5c4c5170e34b10e765-1234567890"

DOCKER_CGROUP_V1 = f"11:memory:/docker/{CONTAINER_ID_64}\n10:cpu,cpuacct:/docker/{CONTAINER_ID_64}\n"

K8S_CGROUP_V1 = (
    f"9:memory:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/{CONTAINER_ID_K8S}\n"
    f"1:name=systemd:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/{CONTAINER_ID_K8S}\n"
)

ECS_CGROUP_V1 = (
    f"8:memory:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/{CONTAINER_ID_64}\n"
    f"1:blkio:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/{CONTAINER_ID_64}\n"
)

# Fargate >= 1.4.0: UUID-formatted task ID as last path component.
FARGATE_14_CGROUP = (
    f"10:pids:/ecs/55091c13-b8cf-4801-b527-f4601742204d/{TASK_ID_FARGATE}\n1:name=systemd:/ecs/{TASK_ID_FARGATE}\n"
)

# k8s with kubepods.slice / docker-<hex>.scope paths.
K8S_SLICE_SCOPE = (
    f"1:name=systemd:/kubepods.slice/kubepods-burstable.slice/"
    f"kubepods-burstable-pod2d3da189_6407_48e3_9ab6_78188d75e609.slice/"
    f"docker-{CONTAINER_ID_64}.scope\n"
)

# cgroup v2 unified hierarchy — no container ID in path.
CGROUP_V2_UNIFIED = "0::/\n"

# Non-containerised Linux session scope.
NON_CONTAINERISED = (
    "11:blkio:/user.slice/user-0.slice/session-14.scope\n"
    "10:memory:/user.slice/user-0.slice/session-14.scope\n"
    "1:name=systemd:/user.slice/user-0.slice/session-14.scope\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_cgroup(content: str) -> str:
    fp = tempfile.NamedTemporaryFile(mode="w", suffix=".cgroup", delete=False)
    fp.write(content)
    fp.close()
    return fp.name


def _read(content: str) -> Optional[str]:
    """Write ``content`` to a temp file and run _read_container_id against it."""
    reader = ContainerID.__new__(ContainerID)
    path = _write_cgroup(content)
    try:
        return reader._read_container_id(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Happy-path: ID is present
# ---------------------------------------------------------------------------


class TestReadContainerIdFound:
    def test_docker_cgroup_v1(self) -> None:
        assert _read(DOCKER_CGROUP_V1) == CONTAINER_ID_64

    def test_k8s_cgroup_v1(self) -> None:
        assert _read(K8S_CGROUP_V1) == CONTAINER_ID_K8S

    def test_ecs_classic(self) -> None:
        assert _read(ECS_CGROUP_V1) == CONTAINER_ID_64

    def test_fargate_14_uuid_task_id(self) -> None:
        assert _read(FARGATE_14_CGROUP) == TASK_ID_FARGATE

    def test_k8s_slice_scope_path(self) -> None:
        assert _read(K8S_SLICE_SCOPE) == CONTAINER_ID_64


# ---------------------------------------------------------------------------
# None paths: file is readable but no container ID
# ---------------------------------------------------------------------------


class TestReadContainerIdNotFound:
    def test_cgroup_v2_unified(self) -> None:
        assert _read(CGROUP_V2_UNIFIED) is None

    def test_non_containerised_linux(self) -> None:
        assert _read(NON_CONTAINERISED) is None

    def test_empty_file(self) -> None:
        assert _read("") is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestReadContainerIdErrors:
    def test_file_not_found_returns_none(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        result = reader._read_container_id("/nonexistent/proc/self/cgroup")
        assert result is None

    def test_non_enoent_io_error_raises_not_implemented(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        io_err = IOError()
        io_err.errno = errno.EACCES
        with mock.patch("builtins.open", side_effect=io_err):
            try:
                reader._read_container_id("/some/path")
                assert False, "expected NotImplementedError"
            except NotImplementedError:
                pass

    def test_unexpected_exception_raises_unresolvable(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        with mock.patch("builtins.open", side_effect=RuntimeError("boom")):
            try:
                reader._read_container_id("/some/path")
                assert False, "expected UnresolvableContainerID"
            except UnresolvableContainerID:
                pass
