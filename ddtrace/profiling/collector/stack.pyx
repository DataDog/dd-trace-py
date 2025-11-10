"""CPU profiling collector."""
from __future__ import absolute_import

import logging
import typing

from ddtrace.trace import Tracer
from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import stack_v2
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector import threading


LOG = logging.getLogger(__name__)


class StackCollector(collector.Collector):
    """Execution stacks collector."""

    __slots__ = (
        "nframes",
        "tracer",
    )

    def __init__(self,
                 nframes: int = config.max_frames,
                 tracer: typing.Optional[Tracer] = None):
        super().__init__()

        self.nframes: int = nframes
        self.tracer: typing.Optional[Tracer] = tracer


    def __repr__(self):
        class_name = self.__class__.__name__
        attrs = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        attrs_str = ", ".join(f"{k}={v!r}" for k, v in attrs.items())

        slot_attrs = {slot: getattr(self, slot) for slot in self.__slots__ if not slot.startswith("_")}
        slot_attrs_str = ", ".join(f"{k}={v!r}" for k, v in slot_attrs.items())

        return f"{class_name}({attrs_str}, {slot_attrs_str})"


    def _init(self):
        # type: (...) -> None
        if self.tracer is not None:
            link_span = stack_v2.link_span
            core.on("ddtrace.context_provider.activate", link_span)

        # stack v2 requires us to patch the Threading module.  It's possible to do this from the stack v2 code
        # itself, but it's a little bit fiddly and it's easier to make it correct here.
        # TODO take the `threading` import out of here and just handle it in v2 startup
        threading.init_stack_v2()
        stack_v2.set_adaptive_sampling(config.stack.v2_adaptive_sampling)
        stack_v2.start()

    def _start_service(self):
        # type: (...) -> None
        # This is split in its own function to ease testing
        LOG.debug("Profiling StackCollector starting")
        self._init()
        super(StackCollector, self)._start_service()
        LOG.debug("Profiling StackCollector started")

    def _stop_service(self):
        # type: (...) -> None
        LOG.debug("Profiling StackCollector stopping")
        super(StackCollector, self)._stop_service()
        if self.tracer is not None:
            link_span = stack_v2.link_span
            core.reset_listeners("ddtrace.context_provider.activate", link_span)
        LOG.debug("Profiling StackCollector stopped")

        # Also tell the native thread running the v2 sampler to stop, if needed
        stack_v2.stop()

    def collect(self):
        return []
