import typing

from .. import recorder


class ExportError(Exception):
    pass


class Exporter:
    """Exporter base class."""

    def export(
        self,
        events: recorder.EventsType,
        start_time_ns: int,
        end_time_ns: int,
    ) -> typing.Any:
        """Export events.

        :param events: List of events to export.
        :param start_time_ns: The start time of recording.
        :param end_time_ns: The end time of recording.
        """
        raise NotImplementedError


class NullExporter(Exporter):
    """Exporter that does nothing."""

    def export(
        self,
        events: recorder.EventsType,
        start_time_ns: int,
        end_time_ns: int,
    ):
        """Discard events."""
        pass
