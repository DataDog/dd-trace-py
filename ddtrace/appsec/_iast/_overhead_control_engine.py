"""
The Overhead control engine (OCE) is an element that by design ensures that the overhead does not go over a maximum
limit. It will measure operations being executed in a request and it will deactivate detection
(and therefore reduce the overhead to nearly 0) if a certain threshold is reached.
"""

from typing import Optional

from ddtrace.appsec._iast._utils import _is_iast_debug_enabled
from ddtrace.internal._unpatched import _threading as threading
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings.asm import config as asm_config


log = get_logger(__name__)


def get_request_sampling_value() -> float:
    # Percentage of requests analyzed by IAST (default: 30%)
    return float(asm_config._iast_request_sampling)


def _get_request_sampling_threshold() -> float:
    sample_rate = min(1.0, max(0.0, get_request_sampling_value() / 100.0))
    return sample_rate * MAX_UINT_64BITS


class OverheadControl:
    """Select requests for IAST analysis independently of trace retention.

    Capture the configured percentage until reconfiguration. Requests with the
    same low 64 trace-ID bits receive the same admission decision.
    """

    _lock = threading.Lock()
    _request_sampling_threshold = _get_request_sampling_threshold()

    def reconfigure(self) -> None:
        self._request_sampling_threshold = _get_request_sampling_threshold()

    def acquire_request(self, span: Optional[SpanData]) -> bool:
        """Decide whether if IAST analysis will be done for this request.
        - Use sample rating to analyze only a percentage of the total requests (30% by default).
        """
        if (
            span
            and (span._trace_id_64bits * SAMPLING_KNUTH_FACTOR) % SAMPLING_HASH_MODULO
            > self._request_sampling_threshold
        ):
            if _is_iast_debug_enabled():
                log.debug("iast::propagation::context::Skip request by sampling rate")
            return False

        return True


oce = OverheadControl()
