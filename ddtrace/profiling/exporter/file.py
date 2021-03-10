import gzip
import os

from ddtrace.profiling.exporter import pprof
from ddtrace.vendor import attr


@attr.s
class PprofFileExporter(pprof.PprofExporter):
    """PProf file exporter."""

    prefix = attr.ib()
    _increment = attr.ib(default=1, init=False, repr=False)

    def export(self, events, start_time_ns, end_time_ns):
        # type: (...) -> None
        """Export events to pprof file.

        The file name is based on the prefix passed to init. The process ID number and type of export is then added as a
        suffix.

        :param events: The event dictionary from a `ddtrace.profiling.recorder.Recorder`.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        profile = super(PprofFileExporter, self).export(events, start_time_ns, end_time_ns)
        with gzip.open(self.prefix + (".%d.%d" % (os.getpid(), self._increment)), "wb") as f:
            f.write(profile.SerializeToString())  # type: ignore[attr-defined]
        self._increment += 1
