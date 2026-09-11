import abc
from dataclasses import dataclass
from itertools import count
from threading import Lock
from threading import Thread
import typing as t
from weakref import WeakKeyDictionary

from ddtrace.debugging._probe.model import FunctionLocationMixin
from ddtrace.debugging._probe.model import LineLocationMixin
from ddtrace.debugging._signal.model import Signal
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.internal.runtime import get_runtime_id


# DEV: Thread generation tokens exist because ``Thread.ident`` is a recycled
# pthread handle: a thread that ends at T1 can have its ident reused by an
# unrelated thread created at T2, so two snapshots sharing a ``thread_id`` are
# not guaranteed to come from the same execution context. Pairing the ident with
# a generation token makes the ambiguity detectable: same ident + different
# generation means definitely different threads.
#
# The token is a lazily assigned counter rather than a birth timestamp because
# we have no hook at thread creation. Keying on the ``Thread`` object (not its
# ident) is what makes it correct: distinct objects get distinct tokens even
# when their idents collide. Tokens are only unique within a runtime ID, which
# is emitted alongside them.
_generations: t.MutableMapping[Thread, int] = WeakKeyDictionary()
_generation_counter = count(1)
_generation_lock = Lock()


def get_thread_generation(thread: Thread) -> int:
    """Return a token that distinguishes this thread from earlier ident reuses."""
    try:
        return _generations[thread]
    except KeyError:
        pass

    with _generation_lock:
        # Another thread may have assigned a token while we waited for the lock.
        try:
            return _generations[thread]
        except KeyError:
            generation = _generations[thread] = next(_generation_counter)
            return generation


@dataclass
class LogSignal(Signal):
    """A signal that also emits a log message.

    Some signals might require sending a log message along with the base signal
    data. For example, all the collected errors from expression evaluations
    (e.g. conditions) might need to be reported.
    """

    __type__ = "snapshot"
    __track__: t.ClassVar[SignalTrack] = SignalTrack.LOGS

    @property
    @abc.abstractmethod
    def message(self) -> t.Optional[str]:
        """The log message to emit."""
        pass

    @abc.abstractmethod
    def has_message(self) -> bool:
        """Whether the signal has a log message to emit."""
        pass

    @property
    def data(self) -> dict[str, t.Any]:
        """Extra data to include in the snapshot portion of the log message."""
        return {}

    def _probe_details(self) -> dict[str, t.Any]:
        probe = self.probe
        if isinstance(probe, LineLocationMixin):
            location = {
                "file": str(probe.resolved_source_file),
                "lines": [str(probe.line)],
            }
        elif isinstance(probe, FunctionLocationMixin):
            location = {
                "type": probe.module,
                "method": probe.func_qname,
            }
        else:
            return {}

        return {
            "id": probe.probe_id,
            "version": probe.version,
            "location": location,
        }

    @property
    def snapshot(self) -> dict[str, t.Any]:
        full_data = {
            "id": self.uuid,
            "timestamp": int(self.timestamp * 1e3),  # milliseconds
            "evaluationErrors": [{"expr": e.expr, "message": e.message} for e in self.errors],
            "probe": self._probe_details(),
            "language": "python",
            "type": self.__type__,
            # Correlation identifiers. The runtime ID makes a restart within the
            # same container visible; the thread generation disambiguates a
            # recycled thread ident. Must not be cached: the runtime ID changes
            # on fork.
            "runtime_id": get_runtime_id(),
            "thread_generation": get_thread_generation(self.thread),
        }
        full_data.update(self.data)

        return full_data
