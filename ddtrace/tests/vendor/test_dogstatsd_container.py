"""Tests for the vendored DogStatsD container-ID reader.

Covers the cgroup v2 origin-detection fix (new code):
  - ContainerID.__init__: delegates to path-read vs inode-fallback
  - ContainerID._is_host_cgroup_namespace
  - ContainerID._read_cgroup_path
  - ContainerID._get_cgroup_from_inode

And the pre-existing _read_container_id helper:
  - TestReadContainerIdFound
  - TestReadContainerIdNotFound
  - TestReadContainerIdErrors
"""

import errno
import os
import tempfile
from typing import Optional
from unittest import mock

from ddtrace.vendor.dogstatsd.container import ContainerID
from ddtrace.vendor.dogstatsd.container import UnresolvableContainerID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONTAINER_ID_64: str = "3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860"
CONTAINER_ID_K8S: str = "3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1"
TASK_ID_FARGATE: str = "34dc0b5e626f2c5c4c5170e34b10e765-1234567890"

DOCKER_CGROUP_V1: str = f"11:memory:/docker/{CONTAINER_ID_64}\n10:cpu,cpuacct:/docker/{CONTAINER_ID_64}\n"
K8S_CGROUP_V1: str = (
    f"9:memory:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/{CONTAINER_ID_K8S}\n"
    f"1:name=systemd:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/{CONTAINER_ID_K8S}\n"
)
ECS_CGROUP_V1: str = (
    f"8:memory:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/{CONTAINER_ID_64}\n"
    f"1:blkio:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/{CONTAINER_ID_64}\n"
)
FARGATE_14_CGROUP: str = (
    f"10:pids:/ecs/55091c13-b8cf-4801-b527-f4601742204d/{TASK_ID_FARGATE}\n1:name=systemd:/ecs/{TASK_ID_FARGATE}\n"
)
K8S_SLICE_SCOPE: str = (
    f"1:name=systemd:/kubepods.slice/kubepods-burstable.slice/"
    f"kubepods-burstable-pod2d3da189_6407_48e3_9ab6_78188d75e609.slice/"
    f"docker-{CONTAINER_ID_64}.scope\n"
)
CGROUP_V2_UNIFIED: str = "0::/\n"
NON_CONTAINERISED: str = (
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
    reader: ContainerID = ContainerID.__new__(ContainerID)
    path: str = _write_cgroup(content)
    try:
        return reader._read_container_id(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# _is_host_cgroup_namespace
# ---------------------------------------------------------------------------


class TestIsHostCgroupNamespace:
    def test_returns_false_when_ns_path_missing(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        reader.CGROUP_NS_PATH = "/nonexistent/ns/cgroup"
        assert reader._is_host_cgroup_namespace() is False

    def test_returns_false_when_inode_does_not_match(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        with tempfile.NamedTemporaryFile(delete=False) as fp:
            path: str = fp.name
        try:
            reader.CGROUP_NS_PATH = path
            # Real inode of a temp file is never 0xEFFFFFFB.
            assert reader._is_host_cgroup_namespace() is False
        finally:
            os.unlink(path)

    def test_returns_true_when_inode_matches(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        with tempfile.NamedTemporaryFile(delete=False) as fp:
            path: str = fp.name
        try:
            real_inode: int = os.stat(path).st_ino
            reader.CGROUP_NS_PATH = path
            reader.HOST_CGROUP_NAMESPACE_INODE = real_inode
            assert reader._is_host_cgroup_namespace() is True
        finally:
            os.unlink(path)

    def test_returns_false_on_unexpected_exception(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        reader.CGROUP_NS_PATH = "/nonexistent"
        with (
            mock.patch("os.path.exists", return_value=True),
            mock.patch("os.stat", side_effect=PermissionError("denied")),
        ):
            assert reader._is_host_cgroup_namespace() is False


# ---------------------------------------------------------------------------
# _read_cgroup_path
# ---------------------------------------------------------------------------


class TestReadCgroupPath:
    def _make_reader(self, cgroup_content: str) -> ContainerID:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = _write_cgroup(cgroup_content)
        return reader

    def test_returns_ci_prefixed_id_for_docker_cgroup_v1(self) -> None:
        reader: ContainerID = self._make_reader(DOCKER_CGROUP_V1)
        try:
            assert reader._read_cgroup_path() == f"ci-{CONTAINER_ID_64}"
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_ci_prefixed_id_for_k8s_cgroup_v1(self) -> None:
        reader: ContainerID = self._make_reader(K8S_CGROUP_V1)
        try:
            assert reader._read_cgroup_path() == f"ci-{CONTAINER_ID_K8S}"
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_none_for_cgroup_v2_unified(self) -> None:
        reader: ContainerID = self._make_reader(CGROUP_V2_UNIFIED)
        try:
            assert reader._read_cgroup_path() is None
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_none_when_cgroup_file_missing(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = "/nonexistent/cgroup"
        assert reader._read_cgroup_path() is None


# ---------------------------------------------------------------------------
# _get_cgroup_from_inode
# ---------------------------------------------------------------------------


class TestGetCgroupFromInode:
    def _make_reader(self, cgroup_content: str, mount_dir: Optional[str] = None) -> ContainerID:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = _write_cgroup(cgroup_content)
        reader.CGROUP_MOUNT_PATH = mount_dir or tempfile.mkdtemp()
        return reader

    def test_returns_in_prefixed_inode_for_cgroup_v2(self) -> None:
        mount_dir: str = tempfile.mkdtemp()
        reader: ContainerID = self._make_reader(CGROUP_V2_UNIFIED, mount_dir)
        try:
            result: Optional[str] = reader._get_cgroup_from_inode()
            assert result is not None
            assert result.startswith("in-")
            inode: int = int(result[3:])
            assert inode > 2
        finally:
            os.unlink(reader.CGROUP_PATH)
            os.rmdir(mount_dir)

    def test_returns_in_prefixed_inode_for_cgroup_v1_memory_controller(self) -> None:
        # cgroup v1 memory controller path exists → inode lookup succeeds.
        mount_dir: str = tempfile.mkdtemp()
        # Create a sub-directory mimicking /sys/fs/cgroup/memory/<cgroup-node>.
        mem_dir: str = os.path.join(mount_dir, "memory", "docker", "abc123")
        os.makedirs(mem_dir)
        content: str = "5:memory:/docker/abc123\n"
        reader: ContainerID = self._make_reader(content, mount_dir)
        try:
            result: Optional[str] = reader._get_cgroup_from_inode()
            assert result is not None
            assert result.startswith("in-")
        finally:
            os.unlink(reader.CGROUP_PATH)
            os.rmdir(mem_dir)
            os.rmdir(os.path.join(mount_dir, "memory", "docker"))
            os.rmdir(os.path.join(mount_dir, "memory"))
            os.rmdir(mount_dir)

    def test_returns_none_when_cgroup_file_missing(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = "/nonexistent/cgroup"
        reader.CGROUP_MOUNT_PATH = "/nonexistent/sys/fs/cgroup"
        assert reader._get_cgroup_from_inode() is None

    def test_returns_none_when_mount_path_missing(self) -> None:
        reader: ContainerID = self._make_reader(CGROUP_V2_UNIFIED, "/nonexistent/mount")
        try:
            assert reader._get_cgroup_from_inode() is None
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_none_when_inode_is_root(self) -> None:
        # Simulate an inode <= 2 (root of a filesystem), which should be skipped.
        reader: ContainerID = self._make_reader(CGROUP_V2_UNIFIED)
        try:
            with mock.patch("os.stat") as mock_stat:
                mock_stat.return_value = mock.Mock(st_ino=2)
                assert reader._get_cgroup_from_inode() is None
        finally:
            os.unlink(reader.CGROUP_PATH)
            os.rmdir(reader.CGROUP_MOUNT_PATH)


# ---------------------------------------------------------------------------
# __init__ (branching logic)
# ---------------------------------------------------------------------------


class TestContainerIDInit:
    def test_uses_cgroup_path_when_parseable(self) -> None:
        # ci- path is always tried first; inode is never consulted when it succeeds.
        with (
            mock.patch.object(ContainerID, "_read_cgroup_path", return_value=f"ci-{CONTAINER_ID_64}") as mock_path,
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace") as mock_ns,
            mock.patch.object(ContainerID, "_get_cgroup_from_inode") as mock_inode,
        ):
            reader: ContainerID = ContainerID()
            assert reader.container_id == f"ci-{CONTAINER_ID_64}"
            mock_path.assert_called_once()
            mock_ns.assert_not_called()
            mock_inode.assert_not_called()

    def test_uses_inode_fallback_when_path_empty_and_not_host_namespace(self) -> None:
        # Typical cgroup v2: path yields None and namespace check is private → inode.
        with (
            mock.patch.object(ContainerID, "_read_cgroup_path", return_value=None) as mock_path,
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace", return_value=False),
            mock.patch.object(ContainerID, "_get_cgroup_from_inode", return_value="in-99999") as mock_inode,
        ):
            reader: ContainerID = ContainerID()
            assert reader.container_id == "in-99999"
            mock_path.assert_called_once()
            mock_inode.assert_called_once()

    def test_skips_inode_when_host_namespace_and_path_empty(self) -> None:
        # In the host namespace without a parseable path (bare non-container Linux),
        # the inode fallback is skipped.
        with (
            mock.patch.object(ContainerID, "_read_cgroup_path", return_value=None),
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace", return_value=True),
            mock.patch.object(ContainerID, "_get_cgroup_from_inode") as mock_inode,
        ):
            reader: ContainerID = ContainerID()
            assert reader.container_id is None
            mock_inode.assert_not_called()

    def test_container_id_is_none_when_inode_fallback_returns_none(self) -> None:
        with (
            mock.patch.object(ContainerID, "_read_cgroup_path", return_value=None),
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace", return_value=False),
            mock.patch.object(ContainerID, "_get_cgroup_from_inode", return_value=None),
        ):
            reader: ContainerID = ContainerID()
            assert reader.container_id is None


# ---------------------------------------------------------------------------
# _read_container_id
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


class TestReadContainerIdNotFound:
    def test_cgroup_v2_unified(self) -> None:
        assert _read(CGROUP_V2_UNIFIED) is None

    def test_non_containerised_linux(self) -> None:
        assert _read(NON_CONTAINERISED) is None

    def test_empty_file(self) -> None:
        assert _read("") is None


class TestReadContainerIdErrors:
    def test_file_not_found_returns_none(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        assert reader._read_container_id("/nonexistent/proc/self/cgroup") is None

    def test_non_enoent_io_error_raises_not_implemented(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        io_err: IOError = IOError()
        io_err.errno = errno.EACCES
        with mock.patch("builtins.open", side_effect=io_err):
            try:
                reader._read_container_id("/some/path")
                assert False, "expected NotImplementedError"
            except NotImplementedError:
                pass

    def test_unexpected_exception_raises_unresolvable(self) -> None:
        reader: ContainerID = ContainerID.__new__(ContainerID)
        with mock.patch("builtins.open", side_effect=RuntimeError("boom")):
            try:
                reader._read_container_id("/some/path")
                assert False, "expected UnresolvableContainerID"
            except UnresolvableContainerID:
                pass
