# -*- encoding: utf-8 -*-
import collections
import os

from ddtrace.internal import nogevent
from ddtrace.vendor import attr


class _defaultdictkey(dict):
    """A variant of defaultdict that calls default_factory with the missing key as argument."""

    def __init__(self, default_factory=None):
        self.default_factory = default_factory

    def __missing__(self, key):
        if self.default_factory:
            v = self[key] = self.default_factory(key)
            return v
        raise KeyError(key)


@attr.s(slots=True)
class Recorder(object):
    """An object that records program activity."""

    _DEFAULT_MAX_EVENTS = 32768

    default_max_events = attr.ib(default=_DEFAULT_MAX_EVENTS)
    """The maximum number of events for an event type if one is not specified."""

    max_events = attr.ib(factory=dict)
    """A dict of {event_type_class: max events} to limit the number of events to record."""

    events = attr.ib(init=False, repr=False, eq=False)
    _events_lock = attr.ib(init=False, repr=False, factory=nogevent.DoubleLock, eq=False)
    _pid = attr.ib(init=False, repr=False, factory=os.getpid)

    def __attrs_post_init__(self):
        self._reset_events()

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
        # NOTE: do not try to push events if the current PID has changed
        # This means:
        # 1. the process has forked
        # 2. we don't know the state of _events_lock and it might be unusable — we'd deadlock
        if events and os.getpid() == self._pid:
            event_type = events[0].__class__
            with self._events_lock:
                q = self.events[event_type]
                q.extend(events)

    def _get_deque_for_event_type(self, event_type):
        return collections.deque(maxlen=self.max_events.get(event_type, self.default_max_events))

    def _reset_events(self):
        self.events = _defaultdictkey(self._get_deque_for_event_type)

    def reset(self):
        """Reset the recorder.

        This is useful when e.g. exporting data. Once the event queue is retrieved, a new one can be created by calling
        the reset method, avoiding iterating on a mutating event list.

        :return: The list of events that has been removed.
        """
        with self._events_lock:
            events = self.events
            self._reset_events()
        return events
