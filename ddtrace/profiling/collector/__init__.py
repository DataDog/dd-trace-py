# -*- encoding: utf-8 -*-
from ddtrace.profiling import _attr
from ddtrace.profiling import _periodic
from ddtrace.profiling import _service
from ddtrace.vendor import attr


@attr.s
class Collector(_service.Service):
    """A profile collector."""

    recorder = attr.ib()


@attr.s(slots=True)
class PeriodicCollector(Collector, _periodic.PeriodicService):
    """A collector that needs to run periodically."""

    def periodic(self):
        """Collect events and push them into the recorder."""
        for events in self.collect():
            self.recorder.push_events(events)

    @staticmethod
    def collect():
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
    capture_pct = attr.ib(factory=_attr.from_env("DD_PROFILING_CAPTURE_PCT", 2, float))
    _capture_sampler = attr.ib(default=attr.Factory(_create_capture_sampler, takes_self=True), init=False, repr=False)
