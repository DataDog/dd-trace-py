from ddtrace.vendor import attr


class ExportError(Exception):
    pass


@attr.s
class Exporter(object):
    """Exporter base class."""

    @staticmethod
    def export(events, start_time_ns, end_time_ns):
        """Export events.

        :param events: List of events to export.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        raise NotImplementedError


@attr.s
class NullExporter(Exporter):
    """Exporter that does nothing."""

    @staticmethod
    def export(events, start_time_ns, end_time_ns):
        """Discard events."""
        pass
