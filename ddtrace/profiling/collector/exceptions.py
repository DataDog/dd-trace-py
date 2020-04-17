from __future__ import absolute_import

import sys

from ddtrace.profiling import _attr
from ddtrace.profiling import collector
from ddtrace.profiling import event
from ddtrace.vendor import attr
from ddtrace.profiling.collector import _traceback
from ddtrace.profiling.collector import threading


@event.event_class
class UncaughtExceptionEvent(event.Event):
    """A lock has been acquired."""

    frames = attr.ib(default=None)
    nframes = attr.ib(default=None)
    exc_type = attr.ib(default=None)
    thread_id = attr.ib(default=None)
    thread_name = attr.ib(default=None)


@attr.s
class UncaughtExceptionCollector(collector.Collector):
    """Record uncaught thrown exceptions."""

    max_nframes = attr.ib(factory=_attr.from_env("DD_PROFILING_MAX_FRAMES", 64, int))

    def start(self):
        """Start collecting uncaught exceptions."""
        self.original_except_hook = sys.excepthook
        sys.excepthook = self.except_hook
        super(UncaughtExceptionCollector, self).start()

    def stop(self):
        """Stop collecting uncaught exceptions."""
        if hasattr(self, "original_except_hook"):
            sys.excepthook = self.original_except_hook
            del self.original_except_hook
        super(UncaughtExceptionCollector, self).stop()

    def except_hook(self, exctype, value, traceback):
        try:
            frames, nframes = _traceback.traceback_to_frames(traceback, self.max_nframes)
            thread_id, thread_name = threading._current_thread()
            self.recorder.push_event(
                UncaughtExceptionEvent(
                    frames=frames, nframes=nframes, thread_id=thread_id, thread_name=thread_name, exc_type=exctype
                )
            )
        finally:
            return self.original_except_hook(exctype, value, traceback)
