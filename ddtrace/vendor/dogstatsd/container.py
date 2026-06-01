# Unless explicitly stated otherwise all files in this repository are licensed under
# the BSD-3-Clause License. This product includes software developed at Datadog
# (https://www.datadoghq.com/).
# Copyright 2015-Present Datadog, Inc

import errno
import os
import re
from typing import Optional


class UnresolvableContainerID(Exception):
    """
    Unable to get container ID from cgroup.
    """


class ContainerID(object):
    """
    A reader class that retrieves either:
    - The current container ID parsed from the cgroup file (cgroup v1 / host
      cgroup namespace), prefixed with ``ci-``.
    - The cgroup controller inode (cgroup v2 / non-host cgroup namespace),
      prefixed with ``in-``.

    Both forms are understood by the Datadog Agent's origin detection, which
    uses them to enrich DogStatsD metrics with orchestrator tags such as
    ``pod_name``. The previous implementation only parsed ``/proc/self/cgroup``
    and therefore returned ``None`` on cgroup v2 nodes, which silently disabled
    origin detection for those pods.

    Returns:
    object: ContainerID

    Raises:
        `NotImplementedError`: No proc filesystem is found (non-Linux systems)
        `UnresolvableContainerID`: Unable to read the container ID
    """

    CGROUP_PATH = "/proc/self/cgroup"
    CGROUP_MOUNT_PATH = "/sys/fs/cgroup"  # cgroup mount path.
    CGROUP_NS_PATH = "/proc/self/ns/cgroup"  # path to the cgroup namespace file.
    CGROUPV1_BASE_CONTROLLER = "memory"  # controller used to identify the container-id in cgroup v1.
    CGROUPV2_BASE_CONTROLLER = ""  # controller used to identify the container-id in cgroup v2.
    HOST_CGROUP_NAMESPACE_INODE = 0xEFFFFFFB  # inode of the host cgroup namespace.

    UUID_SOURCE = r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12}"
    CONTAINER_SOURCE = r"[0-9a-f]{64}"
    TASK_SOURCE = r"[0-9a-f]{32}-\d+"
    LINE_RE = re.compile(r"^(\d+):([^:]*):(.+)$")
    CONTAINER_RE = re.compile(r"(?:.+)?({0}|{1}|{2})(?:\.scope)?$".format(UUID_SOURCE, CONTAINER_SOURCE, TASK_SOURCE))

    def __init__(self) -> None:
        if self._is_host_cgroup_namespace():
            self.container_id = self._read_cgroup_path()
            return
        self.container_id = self._get_cgroup_from_inode()

    def _is_host_cgroup_namespace(self) -> bool:
        """Check whether the current process is in the host cgroup namespace.

        When it is (cgroup v1, or v2 without a private namespace) the container
        ID can be parsed from the cgroup path. Otherwise (typical cgroup v2)
        we must fall back to the cgroup controller inode.
        """
        try:
            return (
                os.stat(self.CGROUP_NS_PATH).st_ino == self.HOST_CGROUP_NAMESPACE_INODE
                if os.path.exists(self.CGROUP_NS_PATH)
                else False
            )
        except Exception:
            return False

    def _read_cgroup_path(self) -> Optional[str]:
        """Read the container ID from the cgroup file, prefixed with ``ci-``."""
        container_id = self._read_container_id(self.CGROUP_PATH)
        return "ci-{0}".format(container_id) if container_id else None

    def _get_cgroup_from_inode(self) -> Optional[str]:
        """Read the container ID from the cgroup controller inode (``in-<inode>``).

        This is the cgroup v2 fallback: on those nodes ``/proc/self/cgroup``
        does not contain a parseable container ID, so we resolve the inode of
        the cgroup controller node, which the Agent maps back to the container.
        """
        try:
            # Map each base controller to its associated cgroup node path.
            cgroup_controllers_paths: dict[str, str] = {}
            with open(self.CGROUP_PATH, mode="r") as fp:
                for line in fp:
                    tokens = line.strip().split(":")
                    if len(tokens) != 3:
                        continue
                    if tokens[1] in (self.CGROUPV1_BASE_CONTROLLER, self.CGROUPV2_BASE_CONTROLLER):
                        cgroup_controllers_paths[tokens[1]] = tokens[2]

            for controller in (self.CGROUPV1_BASE_CONTROLLER, self.CGROUPV2_BASE_CONTROLLER):
                if controller in cgroup_controllers_paths:
                    node_path = cgroup_controllers_paths[controller]
                    inode_path = os.path.join(
                        self.CGROUP_MOUNT_PATH,
                        controller,
                        node_path.lstrip("/"),
                    )
                    inode = os.stat(inode_path).st_ino
                    # 0 is not a valid inode. 1 is a bad block inode and 2 is the root of a filesystem.
                    if inode > 2:
                        return "in-{0}".format(inode)
        except Exception:
            return None
        return None

    def _read_container_id(self, fpath: str) -> Optional[str]:
        try:
            with open(fpath, mode="r") as fp:
                for line in fp:
                    line = line.strip()
                    match = self.LINE_RE.match(line)
                    if not match:
                        continue
                    _, _, path = match.groups()
                    parts = [p for p in path.split("/")]
                    if len(parts):
                        match = self.CONTAINER_RE.match(parts.pop())
                        if match:
                            return match.group(1)
        except IOError as e:
            if e.errno != errno.ENOENT:
                raise NotImplementedError("Unable to open {}.".format(self.CGROUP_PATH))
        except Exception as e:
            raise UnresolvableContainerID("Unable to read the container ID: " + str(e))
        return None
