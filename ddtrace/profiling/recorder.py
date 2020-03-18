# -*- encoding: utf-8 -*-
import collections

from ddtrace.profiling import _attr
from ddtrace.vendor import attr


@attr.s(slots=True, eq=False)
class Recorder(object):
    """An object that records program activity."""

    events = attr.ib(init=False, repr=False)
    max_size = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_EVENTS", 49152, int))
    event_filters = attr.ib(factory=lambda: collections.defaultdict(list), repr=False)

    def __attrs_post_init__(self):
        self._reset_events()

    def add_event_filter(self, event_type, filter_fn):
        """Add an event filter function.

        A filter function must accept a lists of events as argument and returns a list of events that should be pushed
        into the recorder.

        :param event_type: A class of event.
        :param filter_fn: A filter function to append.

        """
        self.event_filters[event_type].append(filter_fn)

    def remove_event_filter(self, event_type, filter_fn):
        """Remove an event filter from the recorder.

        :param event_type: A class of event.
        :param filter_fn: The filter function to remove.
        """
        self.event_filters[event_type].remove(filter_fn)

    def push_event(self, event):
        """Push an event in the recorder.

        :param event: The `ddtrace.profiling.event.Event` to push.
        """
        return self.push_events([event])

    def push_events(self, events):
        """Push multiple events in the recorder.

        All the events MUST be of the same type.
        There is no sanity check as whether all the events are from the same class for performance reasons.

        :param events: The event list to push.
        """
        if events:
            event_type = events[0].__class__
            for filter_fn in self.event_filters[event_type]:
                events = filter_fn(events)
            q = self.events[event_type]
            q.extend(events)

    def _reset_events(self):
        self.events = collections.defaultdict(lambda: collections.deque(maxlen=self.max_size))

    def reset(self):
        """Reset the recorder.

        This is useful when e.g. exporting data. Once the event queue is retrieved, a new one can be created by calling
        the reset method, avoiding iterating on a mutating event list.

        :return: The list of events that has been removed.
        """
        events = self.events
        self._reset_events()
        return events
