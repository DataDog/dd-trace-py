import errno
from functools import lru_cache
import os
import re
from typing import Any
from typing import Dict
from typing import Literal  # noqa:F401
from typing import Optional
from typing import Union

from ddtrace.internal.constants import CONTAINER_ID_HEADER_NAME
from ddtrace.internal.constants import ENTITY_ID_HEADER_NAME
from ddtrace.internal.constants import EXTERNAL_ENV_ENVIRONMENT_VARIABLE
from ddtrace.internal.constants import EXTERNAL_ENV_HEADER_NAME
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class CGroupInfo:
    id: Any
    groups: Any
    path: Any
    container_id: Any
    controllers: Any
    pod_id: Any
    node_inode: Any
    __slots__ = ("id", "groups", "path", "container_id", "controllers", "pod_id", "node_inode")

    # The second part is the PCF/Garden regexp. We currently assume no suffix ($) to avoid matching pod UIDs
    # See https://github.com/DataDog/datadog-agent/blob/7.40.x/pkg/util/cgroups/reader.go#L50
    UUID_SOURCE_PATTERN = (
        r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12}|([0-9a-f]{8}(-[0-9a-f]{4}){4}$)"
    )
    CONTAINER_SOURCE_PATTERN = r"[0-9a-f]{64}"
    TASK_PATTERN = r"[0-9a-f]{32}-\d+"

    LINE_RE = re.compile(r"^(\d+):([^:]*):(.+)$")
    POD_RE = re.compile(r"pod({0})(?:\.slice)?$".format(UUID_SOURCE_PATTERN))
    CONTAINER_RE = re.compile(
        r"(?:.+)?({0}|{1}|{2})(?:\.scope)?$".format(UUID_SOURCE_PATTERN, CONTAINER_SOURCE_PATTERN, TASK_PATTERN)
    )

    def __init__(
        self,
        id=None,  # noqa: A002
        groups=None,
        path=None,
        container_id=None,
        controllers=None,
        pod_id=None,
        node_inode=None,
    ):
        self.id = id
        self.groups = groups
        self.path = path
        self.container_id = container_id
        self.controllers = controllers
        self.pod_id = pod_id
        self.node_inode = node_inode

    def __eq__(self, other):
        return (
            isinstance(other, CGroupInfo)
            and self.id == other.id
            and self.groups == other.groups
            and self.path == other.path
            and self.container_id == other.container_id
            and self.controllers == other.controllers
            and self.pod_id == other.pod_id
            and self.node_inode == other.node_inode
        )

    @classmethod
    def from_line(cls, line):
        # type: (str) -> Optional[CGroupInfo]
        """
        Parse a new :class:`CGroupInfo` from the provided line

        :param line: A line from a cgroup file (e.g. /proc/self/cgroup) to parse information from
        :type line: str
        :returns: A :class:`CGroupInfo` object with all parsed data, if the line is valid, otherwise `None`
        :rtype: :class:`CGroupInfo` | None

        """
        # Clean up the line
        line = line.strip()

        # Ensure the line is valid
        match = cls.LINE_RE.match(line)
        if not match:
            return None

        id_, groups, path = match.groups()

        # Parse the controllers from the groups
        controllers = [c.strip() for c in groups.split(",") if c.strip()]

        # Break up the path to grab container_id and pod_id if available
        # e.g. /docker/<container_id>
        # e.g. /kubepods/test/pod<pod_id>/<container_id>
        parts = [p for p in path.split("/")]

        # Grab the container id from the path if a valid id is present
        container_id = None
        if len(parts):
            match = cls.CONTAINER_RE.match(parts.pop())
            if match:
                container_id = match.group(1)

        # Grab the pod id from the path if a valid id is present
        pod_id = None
        if len(parts):
            match = cls.POD_RE.match(parts.pop())
            if match:
                pod_id = match.group(1)

        try:
            node_inode = os.stat(f"/sys/fs/cgroup/{path.strip('/')}").st_ino
        except Exception:
            log.debug("Failed to stat cgroup node file for path %r", path)
            node_inode = None

        return cls(
            id=id_,
            groups=groups,
            path=path,
            container_id=container_id,
            controllers=controllers,
            pod_id=pod_id,
            node_inode=node_inode,
        )


@lru_cache(64)
def get_container_info(pid: Union[Literal["self"], int] = "self") -> Optional[CGroupInfo]:
    """
    Helper to fetch the current container id, if we are running in a container.

    We will parse `/proc/{pid}/cgroup` to determine our container id.

    The results of calling this function are cached.
    """
    try:
        with open(f"/proc/{pid}/cgroup", mode="r") as fp:
            for line in fp:
                info = CGroupInfo.from_line(line)
                if info and (info.container_id or info.node_inode):
                    return info
    except IOError as e:
        if e.errno != errno.ENOENT:
            log.debug("Failed to open cgroup file for pid %r", pid, exc_info=True)
    except Exception:
        log.debug("Failed to parse cgroup file for pid %r", pid, exc_info=True)
    return None


def update_headers(headers: Dict) -> None:
    """Get the container info (either the container ID or the cgroup inode) and add it to the headers."""
    container_info = get_container_info()
    if container_info is not None:
        if container_info.container_id:
            headers.update(
                {
                    CONTAINER_ID_HEADER_NAME: container_info.container_id,
                    ENTITY_ID_HEADER_NAME: f"ci-{container_info.container_id}",
                }
            )
        elif container_info.node_inode:
            headers.update(
                {
                    ENTITY_ID_HEADER_NAME: f"in-{container_info.node_inode}",
                }
            )

    # Get the external environment info from the environment variable and add it
    # to the headers
    external_info = os.environ.get(EXTERNAL_ENV_ENVIRONMENT_VARIABLE)
    if external_info:
        headers.update(
            {
                EXTERNAL_ENV_HEADER_NAME: external_info,
            }
        )
