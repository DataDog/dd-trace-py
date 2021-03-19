from ddtrace.vendor import attr


class ExportError(Exception):
    pass


@attr.s
class Exporter(object):
    """Exporter base class."""

    def export(self, events, start_time_ns, end_time_ns):
        # type: (...) -> None
        """Export events.

        :param events: List of events to export.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        raise NotImplementedError


@attr.s
class NullExporter(Exporter):
    """Exporter that does nothing."""

    def export(self, events, start_time_ns, end_time_ns):
        # type: (...) -> None
        """Discard events."""
        pass
