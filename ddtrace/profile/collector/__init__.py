# -*- encoding: utf-8 -*-
import abc

from ddtrace.vendor import six

from ddtrace.profile import _attr
from ddtrace.profile import _periodic
from ddtrace.vendor import attr


# This ought to use `enum.Enum`, but since it's not available in Python 2, we just use a dumb class.
@attr.s(repr=False)
class CollectorStatus(object):
    """A Collector status."""

    status = attr.ib()

    def __repr__(self):
        return self.status.upper()


CollectorStatus.STOPPED = CollectorStatus("stopped")
CollectorStatus.RUNNING = CollectorStatus("running")


@six.add_metaclass(abc.ABCMeta)
@attr.s(slots=True)
class Collector(object):
    """A profile collector."""

    recorder = attr.ib()
    status = attr.ib(default=CollectorStatus.STOPPED, type=CollectorStatus, repr=False, init=False)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.stop()

    @staticmethod
    def _init():
        pass

    @abc.abstractmethod
    def start(self):
        """Start collecting profiles."""
        if self.status == CollectorStatus.RUNNING:
            raise RuntimeError("Collector is already running")
        self.status = CollectorStatus.RUNNING
        self._init()

    @abc.abstractmethod
    def stop(self):
        """Stop collecting profiles."""
        self.status = CollectorStatus.STOPPED


@attr.s(slots=True)
class PeriodicCollector(Collector):
    """A collector that needs to run periodically."""

    _real_thread = False

    _interval = attr.ib(repr=False)
    _worker = attr.ib(default=None, init=False, repr=False)

    def start(self):
        """Start the periodic collector."""
        super(PeriodicCollector, self).start()
        periodic_thread_class = _periodic.PeriodicRealThread if self._real_thread else _periodic.PeriodicThread
        self._worker = periodic_thread_class(
            self.interval, target=self.collect, name="%s:%s" % (__name__, self.__class__.__name__)
        )
        self._worker.start()

    @property
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, value):
        self._interval = value
        # Update the interval of the PeriodicThread based on ours
        if self._worker:
            self._worker.interval = value

    def stop(self):
        """Stop the periodic collector."""
        if self._worker:
            self._worker.stop()
            self._worker.join()
            self._worker = None
        super(PeriodicCollector, self).stop()

    def collect(self):
        """Collect events and push them into the recorder."""
        for events in self._collect():
            self.recorder.push_events(events)

    @staticmethod
    def _collect():
        """Collect the actual data.

        :return: A list of sample list to push in the recorder.
        """
        raise NotImplementedError


@attr.s
class CaptureSampler(object):
    """Determine the events that should be captured based on a sampling percentage."""

    capture_pct = attr.ib(default=100)
    _counter = attr.ib(default=0, init=False)

    @capture_pct.validator
    def capture_pct_validator(self, attribute, value):
        if value < 0 or value > 100:
            raise ValueError("Capture percentage should be between 0 and 100 included")

    def capture(self):
        self._counter += self.capture_pct
        if self._counter >= 100:
            self._counter -= 100
            return True
        return False


def _create_capture_sampler(collector):
    return CaptureSampler(collector.capture_pct)


@attr.s
class CaptureSamplerCollector(Collector):
    capture_pct = attr.ib(factory=_attr.from_env("DD_PROFILING_CAPTURE_PCT", 5, float))
    _capture_sampler = attr.ib(default=attr.Factory(_create_capture_sampler, takes_self=True), init=False, repr=False)
