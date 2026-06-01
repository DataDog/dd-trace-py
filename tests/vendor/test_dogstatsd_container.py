"""Tests for the vendored DogStatsD container-ID reader.

Coverage for code added in the cgroup v2 origin-detection fix:
  - ContainerID.__init__: delegates to path-read vs inode-fallback
  - ContainerID._is_host_cgroup_namespace
  - ContainerID._read_cgroup_path
  - ContainerID._get_cgroup_from_inode

Tests for the pre-existing _read_container_id helper live in a separate PR to
keep this change focused on the new code.
"""

import os
import tempfile
from typing import Optional
from unittest import mock

from ddtrace.vendor.dogstatsd.container import ContainerID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DOCKER_CGROUP_V1 = (
    "11:memory:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860\n"
    "10:cpu,cpuacct:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860\n"
)
K8S_CGROUP_V1 = "9:memory:/kubepods/test/pod3d274242/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1\n"
CGROUP_V2_UNIFIED = "0::/\n"


def _write_cgroup(content: str) -> str:
    fp = tempfile.NamedTemporaryFile(mode="w", suffix=".cgroup", delete=False)
    fp.write(content)
    fp.close()
    return fp.name


# ---------------------------------------------------------------------------
# _is_host_cgroup_namespace
# ---------------------------------------------------------------------------


class TestIsHostCgroupNamespace:
    def test_returns_false_when_ns_path_missing(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        reader.CGROUP_NS_PATH = "/nonexistent/ns/cgroup"
        assert reader._is_host_cgroup_namespace() is False

    def test_returns_false_when_inode_does_not_match(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        with tempfile.NamedTemporaryFile(delete=False) as fp:
            path = fp.name
        try:
            reader.CGROUP_NS_PATH = path
            # Real inode of a temp file is never 0xEFFFFFFB.
            assert reader._is_host_cgroup_namespace() is False
        finally:
            os.unlink(path)

    def test_returns_true_when_inode_matches(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        with tempfile.NamedTemporaryFile(delete=False) as fp:
            path = fp.name
        try:
            real_inode = os.stat(path).st_ino
            reader.CGROUP_NS_PATH = path
            reader.HOST_CGROUP_NAMESPACE_INODE = real_inode
            assert reader._is_host_cgroup_namespace() is True
        finally:
            os.unlink(path)

    def test_returns_false_on_unexpected_exception(self) -> None:
        reader = ContainerID.__new__(ContainerID)
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
        reader = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = _write_cgroup(cgroup_content)
        return reader

    def test_returns_ci_prefixed_id_for_docker_cgroup_v1(self) -> None:
        reader = self._make_reader(DOCKER_CGROUP_V1)
        try:
            result = reader._read_cgroup_path()
            assert result == "ci-3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860"
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_ci_prefixed_id_for_k8s_cgroup_v1(self) -> None:
        reader = self._make_reader(K8S_CGROUP_V1)
        try:
            result = reader._read_cgroup_path()
            assert result == "ci-3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1"
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_none_for_cgroup_v2_unified(self) -> None:
        reader = self._make_reader(CGROUP_V2_UNIFIED)
        try:
            assert reader._read_cgroup_path() is None
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_none_when_cgroup_file_missing(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = "/nonexistent/cgroup"
        assert reader._read_cgroup_path() is None


# ---------------------------------------------------------------------------
# _get_cgroup_from_inode
# ---------------------------------------------------------------------------


class TestGetCgroupFromInode:
    def _make_reader(self, cgroup_content: str, mount_dir: Optional[str] = None) -> ContainerID:
        reader = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = _write_cgroup(cgroup_content)
        reader.CGROUP_MOUNT_PATH = mount_dir or tempfile.mkdtemp()
        return reader

    def test_returns_in_prefixed_inode_for_cgroup_v2(self) -> None:
        mount_dir = tempfile.mkdtemp()
        reader = self._make_reader(CGROUP_V2_UNIFIED, mount_dir)
        try:
            result = reader._get_cgroup_from_inode()
            assert result is not None
            assert result.startswith("in-")
            inode = int(result[3:])
            assert inode > 2
        finally:
            os.unlink(reader.CGROUP_PATH)
            os.rmdir(mount_dir)

    def test_returns_in_prefixed_inode_for_cgroup_v1_memory_controller(self) -> None:
        # cgroup v1 memory controller path exists → inode lookup succeeds.
        mount_dir = tempfile.mkdtemp()
        # Create a sub-directory mimicking /sys/fs/cgroup/memory/<cgroup-node>.
        mem_dir = os.path.join(mount_dir, "memory", "docker", "abc123")
        os.makedirs(mem_dir)
        content = "5:memory:/docker/abc123\n"
        reader = self._make_reader(content, mount_dir)
        try:
            result = reader._get_cgroup_from_inode()
            assert result is not None
            assert result.startswith("in-")
        finally:
            os.unlink(reader.CGROUP_PATH)
            os.rmdir(mem_dir)
            os.rmdir(os.path.join(mount_dir, "memory", "docker"))
            os.rmdir(os.path.join(mount_dir, "memory"))
            os.rmdir(mount_dir)

    def test_returns_none_when_cgroup_file_missing(self) -> None:
        reader = ContainerID.__new__(ContainerID)
        reader.CGROUP_PATH = "/nonexistent/cgroup"
        reader.CGROUP_MOUNT_PATH = "/nonexistent/sys/fs/cgroup"
        assert reader._get_cgroup_from_inode() is None

    def test_returns_none_when_mount_path_missing(self) -> None:
        reader = self._make_reader(CGROUP_V2_UNIFIED, "/nonexistent/mount")
        try:
            assert reader._get_cgroup_from_inode() is None
        finally:
            os.unlink(reader.CGROUP_PATH)

    def test_returns_none_when_inode_is_root(self) -> None:
        # Simulate an inode <= 2 (root of a filesystem), which should be skipped.
        reader = self._make_reader(CGROUP_V2_UNIFIED)
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
    def test_uses_cgroup_path_when_host_namespace(self) -> None:
        with (
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace", return_value=True),
            mock.patch.object(ContainerID, "_read_cgroup_path", return_value="ci-abc123") as mock_path,
            mock.patch.object(ContainerID, "_get_cgroup_from_inode") as mock_inode,
        ):
            reader = ContainerID()
            assert reader.container_id == "ci-abc123"
            mock_path.assert_called_once()
            mock_inode.assert_not_called()

    def test_uses_inode_fallback_when_not_host_namespace(self) -> None:
        with (
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace", return_value=False),
            mock.patch.object(ContainerID, "_read_cgroup_path") as mock_path,
            mock.patch.object(ContainerID, "_get_cgroup_from_inode", return_value="in-99999") as mock_inode,
        ):
            reader = ContainerID()
            assert reader.container_id == "in-99999"
            mock_inode.assert_called_once()
            mock_path.assert_not_called()

    def test_container_id_is_none_when_inode_fallback_returns_none(self) -> None:
        with (
            mock.patch.object(ContainerID, "_is_host_cgroup_namespace", return_value=False),
            mock.patch.object(ContainerID, "_get_cgroup_from_inode", return_value=None),
        ):
            reader = ContainerID()
            assert reader.container_id is None
