from collections import defaultdict
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.debugging._probe.model import FunctionProbe
from ddtrace.debugging._probe.model import LineProbe
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._probe.status import ProbeStatusLogger
from ddtrace.internal import forksafe
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)


class ProbeRegistryEntry(object):

    __slots__ = ("probe", "installed", "exc_info", "message")

    def __init__(self, probe):
        # type: (Probe) -> None
        self.probe = probe
        self.installed = False
        self.exc_info = None  # type: Optional[ExcInfoType]
        self.message = None  # type: Optional[str]

    def activate(self):
        # type: () -> None
        return self.probe.activate()

    def deactivate(self):
        # type: () -> None
        return self.probe.deactivate()

    def set_installed(self):
        # type: () -> None
        self.installed = True

    def set_exc_info(self, exc_info):
        # type: (ExcInfoType) -> None
        self.exc_info = exc_info

    def set_message(self, message):
        # type: (str) -> None
        self.message = message


def _get_probe_location(probe):
    # type: (Probe) -> Optional[str]
    if isinstance(probe, LineProbe):
        return probe.source_file
    elif isinstance(probe, FunctionProbe):
        return probe.module
    else:
        raise ValueError("Unsupported probe type: {}".format(type(probe)))


class ProbeRegistry(dict):
    """Keep track of all the registered probes.

    New probes are also registered as pending, on a location basis, until they
    are processed (e.g. installed, generally by some import hook). Pending
    probes can be retrieved with the ``get_pending`` method.
    """

    def __init__(self, status_logger, *args, **kwargs):
        # type: (ProbeStatusLogger, Any, Any) -> None
        """Initialize the probe registry."""
        super(ProbeRegistry, self).__init__(*args, **kwargs)
        self.logger = status_logger

        # Used to keep track of probes pending installation
        self._pending = defaultdict(list)  # type: Dict[str, List[Probe]]

        self._lock = forksafe.RLock()

    def register(self, *probes):
        # type: (Probe) -> None
        """Register a probe."""
        with self._lock:
            for probe in probes:
                if probe in self:
                    # Already registered.
                    continue

                self[probe.probe_id] = ProbeRegistryEntry(probe)

                location = _get_probe_location(probe)
                if location is None:
                    self.set_error(probe, "Unable to resolve location information for probe {}".format(probe.probe_id))
                    continue

                self._pending[location].append(probe)

                self.logger.received(probe)

    def set_installed(self, probe):
        # type: (Probe) -> None
        """Set the installed flag for a probe."""
        with self._lock:
            self[probe.probe_id].set_installed()

            # No longer pending
            self._remove_pending(probe)

            self.logger.installed(probe)

    def set_exc_info(self, probe, exc_info):
        # type: (Probe, ExcInfoType) -> None
        """Set the installed flag for a probe."""
        with self._lock:
            self[probe.probe_id].set_exc_info(exc_info)
            self.logger.error(probe, exc_info=exc_info)

    def set_error(self, probe, message):
        # type: (Probe, str) -> None
        """Set the error message for a probe."""
        with self._lock:
            self[probe.probe_id].set_message(message)
            self.logger.error(probe, message)

    def log_probes_status(self):
        # type: () -> None
        """Log the status of all the probes using the status logger."""
        with self._lock:
            for entry in self.values():
                if entry.installed:
                    self.logger.installed(entry.probe)
                elif entry.exc_info:
                    self.logger.error(entry.probe, exc_info=entry.exc_info)
                elif entry.message:
                    self.logger.error(entry.probe, message=entry.message)
                else:
                    self.logger.received(entry.probe)

    def _remove_pending(self, probe):
        # type: (Probe) -> None
        location = _get_probe_location(probe)

        # Pending probes must have valid location information
        assert location is not None, probe

        pending_probes = self._pending[location]
        try:
            # DEV: Note that this is O(n), which is fine with a conservative
            # number of probes.
            pending_probes.remove(probe)
        except ValueError:
            # The probe wasn't pending
            pass
        if not pending_probes:
            del self._pending[location]

    def has_probes(self, location):
        # type: (str) -> bool
        for entry in self.values():
            if _get_probe_location(entry.probe) == location:
                return True
        return False

    def unregister(self, *probes):
        # type: (Probe) -> List[Probe]
        """Unregister a collection of probes.

        This also ensures that any pending probes are removed if they haven't
        been processed yet.
        """
        unregistered_probes = []
        with self._lock:
            for probe in probes:
                try:
                    entry = self.pop(probe.probe_id)
                except KeyError:
                    # We don't seem to have the probe
                    logger.warning("Tried to unregister unregistered probe %s", probe.probe_id)
                else:
                    probe = entry.probe
                    self._remove_pending(probe)
                    unregistered_probes.append(probe)
        return unregistered_probes

    def get_pending(self, location):
        # type: (str) -> List[Probe]
        """Get the currently pending probes by location."""
        return self._pending[location]

    def __contains__(self, probe):
        # type: (object) -> bool
        """Check if a probe is in the registry."""
        assert isinstance(probe, Probe)

        with self._lock:
            return super(ProbeRegistry, self).__contains__(probe.probe_id)
