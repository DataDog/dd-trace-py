import gzip
import os
import typing  # noqa:F401

from ddtrace.profiling.exporter import pprof

from .. import recorder  # noqa:F401


class PprofFileExporter(pprof.PprofExporter):
    """PProf file exporter."""

    def __init__(self, prefix="profile", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix
        self._increment = 1

    def export(
        self,
        events,  # type: recorder.EventsType
        start_time_ns,  # type: int
        end_time_ns,  # type: int
    ):
        # type: (...) -> typing.Tuple[pprof.pprof_ProfileType, typing.List[pprof.Package]]
        """Export events to pprof file.

        The file name is based on the prefix passed to init. The process ID number and type of export is then added as a
        suffix.

        :param events: The event dictionary from a `ddtrace.profiling.recorder.Recorder`.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        profile, libs = super(PprofFileExporter, self).export(events, start_time_ns, end_time_ns)
        with gzip.open(self.prefix + (".%d.%d" % (os.getpid(), self._increment)), "wb") as f:
            f.write(profile.SerializeToString())
        self._increment += 1
        return profile, libs
