# -*- encoding: utf-8 -*-
import logging

import attr

from ddtrace.internal import compat
from ddtrace.profiling import scheduler
from ddtrace.internal.utils import attr as attr_utils

LOG = logging.getLogger(__name__)


@attr.s
class ServerlessScheduler(scheduler.Scheduler):
    _interval = attr.ib(factory=attr_utils.from_env("DD_PROFILING_UPLOAD_INTERVAL", 60.0, float))
    _init_time = compat.time_ns()

    def periodic(self):
        now = compat.time_ns()
        # Guard against _last_export not being set
        last_export = self._last_export or self._init_time
        if now - last_export >= int(self._interval) * 1e9:
            super(ServerlessScheduler, self).periodic()