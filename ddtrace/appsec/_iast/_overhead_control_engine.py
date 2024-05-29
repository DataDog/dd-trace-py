"""
The Overhead control engine (OCE) is an element that by design ensures that the overhead does not go over a maximum
limit. It will measure operations being executed in a request and it will deactivate detection
(and therefore reduce the overhead to nearly 0) if a certain threshold is reached.
"""
import os
from typing import Set
from typing import Text
from typing import Tuple
from typing import Type

from ddtrace import _threading as threading
from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger
from ddtrace.sampler import RateSampler


log = get_logger(__name__)


def get_request_sampling_value() -> float:
    # Percentage of requests analyzed by IAST (default: 30%)
    return float(os.environ.get("DD_IAST_REQUEST_SAMPLING", 30.0))


MAX_REQUESTS = int(os.environ.get("DD_IAST_MAX_CONCURRENT_REQUESTS", 2))
MAX_VULNERABILITIES_PER_REQUEST = int(os.environ.get("DD_IAST_VULNERABILITIES_PER_REQUEST", 2))


class Operation(object):
    """Common operation related to Overhead Control Engine (OCE). Every vulnerabilities/taint_sinks should inherit
    from this class. OCE instance calls these methods to control the overhead produced in each request.
    """

    _lock = threading.Lock()
    _vulnerability_quota = MAX_VULNERABILITIES_PER_REQUEST
    _reported_vulnerabilities: Set[Tuple[str, int]] = set()

    @classmethod
    def reset(cls):
        cls._vulnerability_quota = MAX_VULNERABILITIES_PER_REQUEST
        cls._reported_vulnerabilities = set()

    @classmethod
    def acquire_quota(cls) -> bool:
        cls._lock.acquire()
        result = False
        if cls._vulnerability_quota > 0:
            cls._vulnerability_quota -= 1
            result = True
        cls._lock.release()
        return result

    @classmethod
    def increment_quota(cls) -> bool:
        cls._lock.acquire()
        result = False
        if cls._vulnerability_quota < MAX_VULNERABILITIES_PER_REQUEST:
            cls._vulnerability_quota += 1
            result = True
        cls._lock.release()
        return result

    @classmethod
    def has_quota(cls) -> bool:
        cls._lock.acquire()
        result = cls._vulnerability_quota > 0
        cls._lock.release()
        return result

    @classmethod
    def is_not_reported(cls, filename: Text, lineno: int) -> bool:
        vulnerability_id = (filename, lineno)
        if vulnerability_id in cls._reported_vulnerabilities:
            return False

        cls._reported_vulnerabilities.add(vulnerability_id)
        return True


class OverheadControl(object):
    """This class is meant to control the overhead introduced by IAST analysis.
    The goal is to do sampling at different levels of the IAST analysis (per process, per request, etc)
    """

    _lock = threading.Lock()
    _request_quota = MAX_REQUESTS
    _vulnerabilities: Set[Type[Operation]] = set()
    _sampler = RateSampler(sample_rate=get_request_sampling_value() / 100.0)

    def reconfigure(self):
        self._sampler = RateSampler(sample_rate=get_request_sampling_value() / 100.0)

    def acquire_request(self, span: Span) -> bool:
        """Decide whether if IAST analysis will be done for this request.
        - Block a request's quota at start of the request to limit simultaneous requests analyzed.
        - Use sample rating to analyze only a percentage of the total requests (30% by default).
        """
        if self._request_quota <= 0 or not self._sampler.sample(span):
            return False

        with self._lock:
            if self._request_quota <= 0:
                return False

            self._request_quota -= 1

        return True

    def release_request(self):
        """increment request's quota at end of the request."""
        with self._lock:
            self._request_quota += 1
        self.vulnerabilities_reset_quota()

    def register(self, klass: Type[Operation]) -> Type[Operation]:
        """Register vulnerabilities/taint_sinks. This set of elements will restart for each request."""
        self._vulnerabilities.add(klass)
        return klass

    def vulnerabilities_reset_quota(self):
        for k in self._vulnerabilities:
            k.reset()
