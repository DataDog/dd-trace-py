"""
The Overhead control engine (OCE) is an element that by design ensures that the overhead does not go over a maximum
limit. It will measure operations being executed in a request and it will deactivate detection
(and therefore reduce the overhead to nearly 0) if a certain threshold is reached.
"""
from ddtrace._trace.sampler import RateSampler
from ddtrace._trace.span import Span
from ddtrace.appsec._iast._utils import _is_iast_debug_enabled
from ddtrace.internal._unpatched import _threading as threading
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


def get_request_sampling_value() -> float:
    # Percentage of requests analyzed by IAST (default: 30%)
    return float(asm_config._iast_request_sampling)


class OverheadControl(object):
    """This class is meant to control the overhead introduced by IAST analysis.
    The goal is to do sampling at different levels of the IAST analysis (per process, per request, etc)
    """

    _lock = threading.Lock()
    _sampler = RateSampler(sample_rate=get_request_sampling_value() / 100.0)

    def reconfigure(self):
        self._sampler = RateSampler(sample_rate=get_request_sampling_value() / 100.0)

    def acquire_request(self, span: Span) -> bool:
        """Decide whether if IAST analysis will be done for this request.
        - Use sample rating to analyze only a percentage of the total requests (30% by default).
        """
        if span and not self._sampler.sample(span):
            if _is_iast_debug_enabled():
                log.debug("iast::propagation::context::Skip request by sampling rate")
            return False

        return True


oce = OverheadControl()
