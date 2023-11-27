# -*- encoding: utf-8 -*-
import typing  # noqa:F401

import attr

from ddtrace.internal import periodic
from ddtrace.internal import service
from ddtrace.settings.profiling import config

from .. import event  # noqa:F401


class CollectorError(Exception):
    pass


class CollectorUnavailable(CollectorError):
    pass


@attr.s
class Collector(service.Service):
    """A profile collector."""

    recorder = attr.ib()

    @staticmethod
    def snapshot():
        """Take a snapshot of collected data.

        :return: A list of sample list to push in the recorder.
        """


@attr.s(slots=True)
class PeriodicCollector(Collector, periodic.PeriodicService):
    """A collector that needs to run periodically."""

    def periodic(self):
        # type: (...) -> None
        """Collect events and push them into the recorder."""
        for events in self.collect():
            self.recorder.push_events(events)

    def collect(self):
        # type: (...) -> typing.Iterable[typing.Iterable[event.Event]]
        """Collect the actual data.

        :return: A list of event list to push in the recorder.
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
    capture_pct = attr.ib(type=float, default=config.capture_pct)
    _capture_sampler = attr.ib(default=attr.Factory(_create_capture_sampler, takes_self=True), init=False, repr=False)
