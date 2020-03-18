import gzip
import os

from ddtrace.vendor import attr
from ddtrace.profile.exporter import pprof


@attr.s
class PprofFileExporter(pprof.PprofExporter):
    """PProf file exporter."""

    prefix = attr.ib()
    _increment = attr.ib(default=1, init=False, repr=False)

    def export(self, events):
        """Export events to pprof file.

        The file name is based on the prefix passed to init. The process ID number and type of export is then added as a
        suffix.

        :param events: The event dictionary from a `ddtrace.profile.recorder.Recorder`.
        """
        profile = super(PprofFileExporter, self).export(events)
        with gzip.open(self.prefix + (".%d.%d" % (os.getpid(), self._increment)), "wb") as f:
            f.write(profile.SerializeToString())
        self._increment += 1
