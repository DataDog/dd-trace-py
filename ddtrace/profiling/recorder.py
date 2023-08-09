# -*- encoding: utf-8 -*-
import collections
import threading
import typing

import attr

from ddtrace.internal import forksafe
from ddtrace.settings.profiling import config

from . import event


class _defaultdictkey(dict):
    """A variant of defaultdict that calls default_factory with the missing key as argument."""

    def __init__(self, default_factory=None):
        self.default_factory = default_factory

    def __missing__(self, key):
        if self.default_factory:
            v = self[key] = self.default_factory(key)
            return v
        raise KeyError(key)


EventsType = typing.Dict[event.Event, typing.Sequence[event.Event]]


@attr.s
class Recorder(object):
    """An object that records program activity."""

    default_max_events = attr.ib(default=config.spec.max_events.default, type=int)
    """The maximum number of events for an event type if one is not specified."""

    max_events = attr.ib(factory=dict, type=typing.Dict[typing.Type[event.Event], typing.Optional[int]])
    """A dict of {event_type_class: max events} to limit the number of events to record."""

    events = attr.ib(init=False, repr=False, eq=False, type=EventsType)
    _events_lock = attr.ib(init=False, repr=False, factory=threading.RLock, eq=False)

    def __attrs_post_init__(self):
        # type: (...) -> None
        self._reset_events()
        forksafe.register(self._after_fork)

    def _after_fork(self):
        # type: (...) -> None
        # NOTE: do not try to push events if the process forked
        # This means we don't know the state of _events_lock and it might be unusable — we'd deadlock
        self.push_events = self._push_events_noop  # type: ignore[assignment]

    def _push_events_noop(self, events):
        pass

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
