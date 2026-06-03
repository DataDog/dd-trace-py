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


class Cgroup(object):
    """
    A reader class that retrieves either:
    - The current container ID parsed from the cgroup file (cgroup v1 / host
      cgroup namespace), returned as the raw hex ID for backward compatibility
      with all Agent versions.
    - The cgroup controller inode (cgroup v2 / non-host cgroup namespace),
      prefixed with ``in-``, understood by Agent >= 7.51.

    Both forms are understood by the Datadog Agent's origin detection, which
    uses them to enrich DogStatsD metrics with orchestrator tags such as
    ``pod_name``.

    Returns:
    object: Cgroup

    Raises:
        `NotImplementedError`: No proc filesystem is found (non-Linux systems)
        `UnresolvableContainerID`: Unable to read the container ID
    """

    CGROUP_PATH = "/proc/self/cgroup"
    CGROUP_MOUNT_PATH = "/sys/fs/cgroup"  # cgroup mount path.
    CGROUP_NS_PATH = "/proc/self/ns/cgroup"  # path to the cgroup namespace file.
    CGROUPV1_BASE_CONTROLLER = "memory"  # controller used to identify the container-id in cgroup v1 (memory).
    CGROUPV2_BASE_CONTROLLER = ""  # controller used to identify the container-id in cgroup v2.
    HOST_CGROUP_NAMESPACE_INODE = 0xEFFFFFFB  # inode of the host cgroup namespace.

    UUID_SOURCE = r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12}"
    CONTAINER_SOURCE = r"[0-9a-f]{64}"
    TASK_SOURCE = r"[0-9a-f]{32}-\d+"
    LINE_RE = re.compile(r"^(\d+):([^:]*):(.+)$")
    CONTAINER_RE = re.compile(r"(?:.+)?({0}|{1}|{2})(?:\.scope)?$".format(UUID_SOURCE, CONTAINER_SOURCE, TASK_SOURCE))

    def __init__(self) -> None:
        # Local patch vs upstream: try the cgroup path first; only consult the
        # inode fallback when the path yields nothing AND we are not in the host
        # cgroup namespace.  Upstream checks the namespace first and skips
        # _read_cgroup_path() entirely when not in the host namespace, which
        # would miss containers that have a private namespace but a still-
        # parseable cgroup path.
        self.container_id = self._read_cgroup_path()
        if self.container_id is None and not self._is_host_cgroup_namespace():
            self.container_id = self._get_cgroup_from_inode()

    def _is_host_cgroup_namespace(self) -> bool:
        """Check if the current process is in a host cgroup namespace."""
        try:
            return (
                os.path.exists(self.CGROUP_NS_PATH)
                and os.stat(self.CGROUP_NS_PATH).st_ino == self.HOST_CGROUP_NAMESPACE_INODE
            )
        except Exception:
            return False

    def _read_cgroup_path(self) -> Optional[str]:
        """Read the raw container ID from the cgroup file.

        Returns the unprefixed hex ID so that the DogStatsD ``|c:`` header
        remains compatible with all Agent versions (pre-7.51 Agents only
        understand the raw form; newer Agents accept it too).

        Local patch vs upstream: upstream wraps the return in ``ci-{id}``.
        We omit that prefix for backward compatibility.
        """
        try:
            with open(self.CGROUP_PATH, mode="r") as fp:
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

    def _get_cgroup_from_inode(self) -> Optional[str]:
        """Read the container ID from the cgroup inode."""
        try:
            # Parse /proc/self/cgroup and get a map of controller to its associated cgroup node path.
            cgroup_controllers_paths = {}
            with open(self.CGROUP_PATH, mode="r") as fp:
                for line in fp:
                    tokens = line.strip().split(":")
                    if len(tokens) != 3:
                        continue
                    if tokens[1] == self.CGROUPV1_BASE_CONTROLLER or tokens[1] == self.CGROUPV2_BASE_CONTROLLER:
                        cgroup_controllers_paths[tokens[1]] = tokens[2]

            # Retrieve the cgroup inode from "/sys/fs/cgroup + controller + cgroupNodePath".
            for controller in [
                self.CGROUPV1_BASE_CONTROLLER,
                self.CGROUPV2_BASE_CONTROLLER,
            ]:
                if controller in cgroup_controllers_paths:
                    node_path = cgroup_controllers_paths[controller]
                    inode_path = os.path.join(
                        self.CGROUP_MOUNT_PATH,
                        controller,
                        # Local patch vs upstream: lstrip("/") instead of
                        # `if != "/" else ""` so that os.path.join does not
                        # discard the mount-dir prefix when node_path is an
                        # absolute path such as "/docker/abc123".
                        node_path.lstrip("/"),
                    )
                    inode = os.stat(inode_path).st_ino
                    # 0 is not a valid inode. 1 is a bad block inode and 2 is the root of a filesystem.
                    if inode > 2:
                        return "in-{0}".format(inode)
        except Exception:
            return None
        return None
