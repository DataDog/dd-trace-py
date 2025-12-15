"""Simple wrapper around stack native extension module."""

import logging
import typing

from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import stack
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import collector
from ddtrace.profiling.collector import threading
from ddtrace.trace import Tracer


LOG = logging.getLogger(__name__)


class StackCollector(collector.Collector):
    """Execution stacks collector."""

    __slots__ = (
        "nframes",
        "tracer",
    )

    def __init__(self, nframes: typing.Optional[int] = None, tracer: typing.Optional[Tracer] = None):
        super().__init__()

        self.nframes = nframes if nframes is not None else config.max_frames
        self.tracer = tracer

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        attrs = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        attrs_str = ", ".join(f"{k}={v!r}" for k, v in attrs.items())

        slot_attrs = {slot: getattr(self, slot) for slot in self.__slots__ if not slot.startswith("_")}
        slot_attrs_str = ", ".join(f"{k}={v!r}" for k, v in slot_attrs.items())

        return f"{class_name}({attrs_str}, {slot_attrs_str})"

    def _init(self) -> None:
        if self.tracer is not None:
            core.on("ddtrace.context_provider.activate", stack.link_span)

        # stack requires us to patch the Threading module.  It's possible to do this from the stack code
        # itself, but it's a little bit fiddly and it's easier to make it correct here.
        # TODO take the `threading` import out of here and just handle it in v2 startup
        threading.init_stack()
        stack.set_adaptive_sampling(config.stack.adaptive_sampling)
        stack.start()

    def _start_service(self) -> None:
        # This is split in its own function to ease testing
        LOG.debug("Profiling StackCollector starting")
        self._init()
        LOG.debug("Profiling StackCollector started")

    def _stop_service(self) -> None:
        LOG.debug("Profiling StackCollector stopping")
        if self.tracer is not None:
            core.reset_listeners("ddtrace.context_provider.activate", stack.link_span)
        LOG.debug("Profiling StackCollector stopped")

        # Tell the native thread running the v2 sampler to stop
        stack.stop()
