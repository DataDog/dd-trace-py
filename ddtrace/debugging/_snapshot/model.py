from threading import Thread
import time
from types import FrameType
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from uuid import uuid4

import attr

from ddtrace.context import Context
from ddtrace.debugging._probe.model import CaptureLimits
from ddtrace.debugging._probe.model import ProbeConditionMixin
from ddtrace.debugging._snapshot import utils
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _capture_context(
    arguments,  # type: List[Tuple[str, Any]]
    _locals,  # type: List[Tuple[str, Any]]
    throwable,  # type: ExcInfoType
    limits=CaptureLimits(),  # type: CaptureLimits
):
    # type: (...) -> Dict[str, Any]
    return {
        "arguments": {
            n: utils.capture_value(v, limits.max_level, limits.max_len, limits.max_size, limits.max_fields)
            for n, v in arguments
        }
        if arguments is not None
        else {},
        "locals": {
            n: utils.capture_value(v, limits.max_level, limits.max_len, limits.max_size, limits.max_fields)
            for n, v in _locals
        }
        if _locals is not None
        else {},
        "throwable": utils.capture_exc_info(throwable),
    }


class ConditionEvaluationError(Exception):
    """Thrown when an error occurs while evaluating a probe condition."""


@attr.s
class Snapshot(object):
    """Raw snapshot.

    Used to collect the minimum amount of information from a firing probe.
    """

    probe = attr.ib(type=ProbeConditionMixin)
    frame = attr.ib(type=FrameType)
    thread = attr.ib(type=Thread)
    exc_info = attr.ib(type=ExcInfoType)
    context = attr.ib(type=Optional[Context])
    entry_capture = attr.ib(type=Optional[dict], default=None)
    return_capture = attr.ib(type=Optional[dict], default=None)
    duration = attr.ib(type=Optional[int], default=None)  # nanoseconds
    timestamp = attr.ib(type=float, factory=time.time)
    snapshot_id = attr.ib(type=str, init=False, factory=lambda: str(uuid4()))

    def evaluate(self, _locals=None):
        # type: (Optional[Dict[str, Any]]) -> bool
        """Evaluate the probe condition against the collected frame."""
        condition = self.probe.condition
        if condition is None:
            return True

        try:
            return bool(condition.eval(_locals or self.frame.f_locals))
        except Exception as e:
            raise ConditionEvaluationError(e)
