# -*- encoding: utf-8 -*-
import typing  # noqa:F401

from ddtrace.internal import periodic
from ddtrace.internal import service
from ddtrace.internal.compat import dataclasses
from ddtrace.settings.profiling import config

from .. import event  # noqa:F401
from ..recorder import Recorder


class CollectorError(Exception):
    pass


class CollectorUnavailable(CollectorError):
    pass


@dataclasses.dataclass
class Collector(service.Service):
    """A profile collector."""

    recorder: Recorder

    @staticmethod
    def snapshot():
        """Take a snapshot of collected data.

        :return: A list of sample list to push in the recorder.
        """


@dataclasses.dataclass(slots=True)
class PeriodicCollector(periodic.PeriodicService, Collector):
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


@dataclasses.dataclass
class CaptureSampler:
    """Determine the events that should be captured based on a sampling percentage."""

    capture_pct: float = 100.0
    _counter: int = dataclasses.field(default=0, init=False)

    def capture(self):
        self._counter += self.capture_pct
        if self._counter >= 100:
            self._counter -= 100
            return True
        return False

    def __post_init__(self):
        if self.capture_pct < 0 or self.capture_pct > 100:
            raise ValueError("Capture percentage should be between 0 and 100 included")


@dataclasses.dataclass
class CaptureSamplerCollector(Collector):
    capture_pct: float = config.capture_pct
    _capture_sampler: CaptureSampler = dataclasses.field(init=False, repr=False)

    def __post_init__(self):
        super().__post_init__()
        self._capture_sampler = CaptureSampler(self.capture_pct)
