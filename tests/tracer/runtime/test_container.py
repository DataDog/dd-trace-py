import mock
import pytest

from ddtrace.internal.runtime.container import CGroupInfo
from ddtrace.internal.runtime.container import get_container_info

from .utils import cgroup_line_valid_test_cases


def get_mock_open(read_data=None):
    mock_open = mock.mock_open(read_data=read_data)
    return mock.patch("ddtrace.internal.runtime.container.open", mock_open)


def test_cgroup_info_init():
    # Assert default all attributes to `None`
    info = CGroupInfo()
    for attr in ("id", "groups", "path", "container_id", "controllers", "pod_id"):
        assert getattr(info, attr) is None

    # Assert init with property sets property
    info = CGroupInfo(container_id="test-container-id")
    assert info.container_id == "test-container-id"


@pytest.mark.parametrize(
    "line,expected_info",
    # Valid generated cases + one off cases
    cgroup_line_valid_test_cases()
    + [
        # Valid, extra spaces
        (
            "    13:name=systemd:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860    ",
            CGroupInfo(
                id="13",
                groups="name=systemd",
                controllers=["name=systemd"],
                path="/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
                container_id="3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
                pod_id=None,
            ),
        ),
        # Valid, bookended newlines
        (
            "\r\n13:name=systemd:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860\r\n",
            CGroupInfo(
                id="13",
                groups="name=systemd",
                controllers=["name=systemd"],
                path="/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
                container_id="3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
                pod_id=None,
            ),
        ),
        # Valid, fargate >= 1.4.0
        (
            "1:name=systemd:/ecs/34dc0b5e626f2c5c4c5170e34b10e765-1234567890",
            CGroupInfo(
                id="1",
                groups="name=systemd",
                controllers=["name=systemd"],
                path="/ecs/34dc0b5e626f2c5c4c5170e34b10e765-1234567890",
                container_id="34dc0b5e626f2c5c4c5170e34b10e765-1234567890",
                pod_id=None,
            ),
        ),
        # Valid, PCF
        (
            "10:freezer:/garden/6f265890-5165-7fab-6b52-18d1",
            CGroupInfo(
                id="10",
                groups="freezer",
                controllers=["freezer"],
                path="/garden/6f265890-5165-7fab-6b52-18d1",
                container_id="6f265890-5165-7fab-6b52-18d1",
                pod_id=None,
            ),
        ),
        # Invalid container_ids
        (
            # One character too short
            "13:name=systemd:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f86986",
            CGroupInfo(
                id="13",
                groups="name=systemd",
                controllers=["name=systemd"],
                path="/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f86986",
                container_id=None,
                pod_id=None,
            ),
        ),
        (
            # Non-hex
            "13:name=systemd:/docker/3726184226f5d3147c25fzyxw5b60097e378e8a720503a5e19ecfdf29f869860",
            CGroupInfo(
                id="13",
                groups="name=systemd",
                controllers=["name=systemd"],
                path="/docker/3726184226f5d3147c25fzyxw5b60097e378e8a720503a5e19ecfdf29f869860",
                container_id=None,
                pod_id=None,
            ),
        ),
        # Invalid id
        (
            # non-digit
            "a:name=systemd:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
            None,
        ),
        (
            # missing
            ":name=systemd:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
            None,
        ),
        # Missing group
        (
            # empty
            "13::/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
            CGroupInfo(
                id="13",
                groups="",
                controllers=[],
                path="/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
                container_id="3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
                pod_id=None,
            ),
        ),
        (
            # missing
            "13:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
            None,
        ),
        # Empty line
        (
            "",
            None,
        ),
    ],
)
def test_cgroup_info_from_line(line, expected_info):
    info = CGroupInfo.from_line(line)
    info2 = CGroupInfo.from_line(line)

    # Check __eq__
    assert info == info2

    if expected_info is None:
        assert info is None, line
    else:
        for attr in ("id", "groups", "path", "container_id", "controllers", "pod_id"):
            assert getattr(info, attr) == getattr(expected_info, attr), line


@pytest.mark.parametrize(
    "file_contents,container_id,node_inode",
    (
        # Docker file
        (
            """
13:name=systemd:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
12:pids:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
11:hugetlb:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
10:net_prio:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
9:perf_event:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
8:net_cls:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
7:freezer:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
6:devices:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
5:memory:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
4:blkio:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
3:cpuacct:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
2:cpu:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
1:cpuset:/docker/3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860
            """,
            "3726184226f5d3147c25fdeab5b60097e378e8a720503a5e19ecfdf29f869860",
            None,
        ),
        # k8s file
        (
            """
11:perf_event:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
10:pids:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
9:memory:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
8:cpu,cpuacct:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
7:blkio:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
6:cpuset:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
5:devices:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
4:freezer:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
3:net_cls,net_prio:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
2:hugetlb:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
1:name=systemd:/kubepods/test/pod3d274242-8ee0-11e9-a8a6-1e68d864ef1a/3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1
            """,
            "3e74d3fd9db4c9dd921ae05c2502fb984d0cde1b36e581b13f79c639da4518a1",
            None,
        ),
        # k8 format with additional characters before task ID
        (
            """
1:name=systemd:/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod2d3da189_6407_48e3_9ab6_78188d75e609.slice/docker-7b8952daecf4c0e44bbcefe1b5c5ebc7b4839d4eefeccefe694709d3809b6199.scope
            """,
            "7b8952daecf4c0e44bbcefe1b5c5ebc7b4839d4eefeccefe694709d3809b6199",
            None,
        ),
        # ECS file
        (
            """
9:perf_event:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
8:memory:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
7:hugetlb:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
6:freezer:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
5:devices:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
4:cpuset:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
3:cpuacct:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
2:cpu:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
1:blkio:/ecs/test-ecs-classic/5a0d5ceddf6c44c1928d367a815d890f/38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce
            """,
            "38fac3e99302b3622be089dd41e7ccf38aff368a86cc339972075136ee2710ce",
            None,
        ),
        # Fargate file < 1.4.0
        (
            """
11:hugetlb:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
10:pids:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
9:cpuset:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
8:net_cls,net_prio:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
7:cpu,cpuacct:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
6:perf_event:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
5:freezer:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
4:devices:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
3:blkio:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
2:memory:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
1:name=systemd:/ecs/55091c13-b8cf-4801-b527-f4601742204d/432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da
            """,
            "432624d2150b349fe35ba397284dea788c2bf66b885d14dfc1569b01890ca7da",
            None,
        ),
        # Fargate file >= 1.4.0
        (
            """
11:hugetlb:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
10:pids:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
9:cpuset:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
8:net_cls,net_prio:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
7:cpu,cpuacct:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
6:perf_event:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
5:freezer:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
4:devices:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
3:blkio:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
2:memory:/ecs/55091c13-b8cf-4801-b527-f4601742204d/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
1:name=systemd:/ecs/34dc0b5e626f2c5c4c5170e34b10e765-1234567890
            """,
            "34dc0b5e626f2c5c4c5170e34b10e765-1234567890",
            None,
        ),
        # PCF file
        (
            """
12:rdma:/
11:net_cls,net_prio:/garden/6f265890-5165-7fab-6b52-18d1
10:freezer:/garden/6f265890-5165-7fab-6b52-18d1
9:devices:/system.slice/garden.service/garden/6f265890-5165-7fab-6b52-18d1
8:blkio:/system.slice/garden.service/garden/6f265890-5165-7fab-6b52-18d1
7:pids:/system.slice/garden.service/garden/6f265890-5165-7fab-6b52-18d1
6:memory:/system.slice/garden.service/garden/6f265890-5165-7fab-6b52-18d1
5:cpuset:/garden/6f265890-5165-7fab-6b52-18d1
4:cpu,cpuacct:/system.slice/garden.service/garden/6f265890-5165-7fab-6b52-18d1
3:perf_event:/garden/6f265890-5165-7fab-6b52-18d1
2:hugetlb:/garden/6f265890-5165-7fab-6b52-18d1
1:name=systemd:/system.slice/garden.service/garden/6f265890-5165-7fab-6b52-18d1
            """,
            None,
            1234,
        ),
        # Linux non-containerized file
        (
            """
11:blkio:/user.slice/user-0.slice/session-14.scope
10:memory:/user.slice/user-0.slice/session-14.scope
9:hugetlb:/
8:cpuset:/
7:pids:/user.slice/user-0.slice/session-14.scope
6:freezer:/
5:net_cls,net_prio:/
4:perf_event:/
3:cpu,cpuacct:/user.slice/user-0.slice/session-14.scope
2:devices:/user.slice/user-0.slice/session-14.scope
1:name=systemd:/user.slice/user-0.slice/session-14.scope
            """,
            None,
            1234,
        ),
        # Empty file
        ("", None, None),
        # Missing file
        (None, None, None),
    ),
)
def test_get_container_info(file_contents, container_id, node_inode):
    with get_mock_open(read_data=file_contents) as mock_open:
        # simulate the file not being found
        if file_contents is None:
            mock_open.side_effect = FileNotFoundError

        info = get_container_info()

        if info is not None:
            assert info.container_id == container_id
            if node_inode is None:
                assert info.node_inode == node_inode
            else:
                assert isinstance(info.node_inode, int)

        mock_open.assert_called_with("/proc/self/cgroup", mode="r")


@pytest.mark.parametrize(
    "pid,file_name",
    (
        ("13", "/proc/13/cgroup"),
        (13, "/proc/13/cgroup"),
        ("self", "/proc/self/cgroup"),
    ),
)
def test_get_container_info_with_pid(pid, file_name):
    # DEV: We need at least 1 line for the loop to call `CGroupInfo.from_line`
    with get_mock_open(read_data="\r\n") as mock_open:
        assert get_container_info(pid=pid) is None

        mock_open.assert_called_once_with(file_name, mode="r")


@mock.patch("ddtrace.internal.runtime.container.CGroupInfo.from_line")
@mock.patch("ddtrace.internal.runtime.container.log")
def test_get_container_info_exception(mock_log, mock_from_line):
    exception = Exception()
    mock_from_line.side_effect = exception

    # DEV: We need at least 1 line for the loop to call `CGroupInfo.from_line`
    with get_mock_open(read_data="\r\n") as mock_open:
        # Assert calling `get_container_info()` does not bubble up the exception
        assert get_container_info() is None

        # Assert we called everything we expected
        mock_from_line.assert_called_once_with("\r\n")
        mock_open.assert_called_once_with("/proc/self/cgroup", mode="r")

        # Ensure we logged the exception
        mock_log.debug.assert_called_once_with("Failed to parse cgroup file for pid %r", "self", exc_info=True)
