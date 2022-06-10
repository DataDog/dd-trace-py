# -*- encoding: utf-8 -*-
import logging

import attr
from ddtrace.internal import compat
from ddtrace.profiling import scheduler
from ddtrace.internal.utils import attr as attr_utils


LOG = logging.getLogger(__name__)


@attr.s
class ServerlessScheduler(scheduler.Scheduler):
    _interval = attr.ib(factory=attr_utils.from_env("DD_SERVERLESS_PROFILING_UPLOAD_INTERVAL", 1, float))
    _total_profiled_seconds = attr.ib(default=0)

    def periodic(self):
        if self._total_profiled_seconds >= self._interval:
            start_time = compat.monotonic()
            try:
                self.flush()
                self._total_profiled_seconds = 0
            finally:
                self.interval = max(0, self._configured_interval - (compat.monotonic() - start_time))
        else:
            self._total_profiled_seconds += 1