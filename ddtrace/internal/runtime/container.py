import errno
import re
from typing import Optional

import attr

from ..logger import get_logger


log = get_logger(__name__)


@attr.s(slots=True)
class CGroupInfo(object):
    """
    CGroup class for container information parsed from a group cgroup file
    """

    id = attr.ib(default=None)
    groups = attr.ib(default=None)
    path = attr.ib(default=None)
    container_id = attr.ib(default=None)
    controllers = attr.ib(default=None)
    pod_id = attr.ib(default=None)

    UUID_SOURCE_PATTERN = r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12}"
    CONTAINER_SOURCE_PATTERN = r"[0-9a-f]{64}"
    TASK_PATTERN = r"[0-9a-f]{32}-\d+"

    LINE_RE = re.compile(r"^(\d+):([^:]*):(.+)$")
    POD_RE = re.compile(r"pod({0})(?:\.slice)?$".format(UUID_SOURCE_PATTERN))
    CONTAINER_RE = re.compile(
        r"(?:.+)?({0}|{1}|{2})(?:\.scope)?$".format(UUID_SOURCE_PATTERN, CONTAINER_SOURCE_PATTERN, TASK_PATTERN)
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

        return cls(id=id_, groups=groups, path=path, container_id=container_id, controllers=controllers, pod_id=pod_id)


def get_container_info(pid="self"):
    # type: (str) -> Optional[CGroupInfo]
    """
    Helper to fetch the current container id, if we are running in a container

    We will parse `/proc/{pid}/cgroup` to determine our container id.

    The results of calling this function are cached

    :param pid: The pid of the cgroup file to parse (default: 'self')
    :type pid: str | int
    :returns: The cgroup file info if found, or else None
    :rtype: :class:`CGroupInfo` | None
    """

    cgroup_file = "/proc/{0}/cgroup".format(pid)

    try:
        with open(cgroup_file, mode="r") as fp:
            for line in fp:
                info = CGroupInfo.from_line(line)
                if info and info.container_id:
                    return info
    except IOError as e:
        if e.errno != errno.ENOENT:
            log.debug("Failed to open cgroup file for pid %r", pid, exc_info=True)
    except Exception:
        log.debug("Failed to parse cgroup file for pid %r", pid, exc_info=True)
    return None
