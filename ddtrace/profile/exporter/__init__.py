from ddtrace.vendor import attr


class ExportError(Exception):
    pass


@attr.s
class Exporter(object):
    """Exporter base class."""

    @staticmethod
    def export(events):
        """Export events.

        :param events: List of events to export.
        """
        raise NotImplementedError


@attr.s
class NullExporter(Exporter):
    """Exporter that does nothing."""

    @staticmethod
    def export(events):
        """Discard events."""
        pass
