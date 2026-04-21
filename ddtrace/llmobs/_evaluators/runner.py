from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.threads import RLock
from ddtrace.llmobs._evaluators.sampler import EvaluatorRunnerSampler
from ddtrace.llmobs._writer import LLMObsSpanEvent
from ddtrace.trace import Span


logger = get_logger(__name__)


class EvaluatorRunner(PeriodicService):
    """Base class for evaluating LLM Observability span events
    This class
    1. parses active evaluators from the environment and initializes these evaluators
    2. triggers evaluator runs over buffered finished spans on each `periodic` call
    """

    def __init__(self, interval: float, llmobs_service=None, evaluators=None):
        super(EvaluatorRunner, self).__init__(interval=interval)
        self._lock = RLock()
        self._buffer: list[tuple[LLMObsSpanEvent, Span]] = []
        self._buffer_limit = 1000

        self.llmobs_service = llmobs_service
        # Lazy executor: importing concurrent.futures at import time loads concurrent.futures.thread
        # and fails scripts/global-lock-detection.py (CI detect_global_locks).
        self._executor = None
        self.sampler = EvaluatorRunnerSampler()
        self.evaluators = [] if evaluators is None else evaluators

        if len(self.evaluators) > 0:
            return

    def start(self, *args, **kwargs):
        if not self.evaluators:
            logger.debug("no evaluators configured, not starting %r", self.__class__.__name__)
            return
        super(EvaluatorRunner, self).start()
        logger.debug("started %r", self.__class__.__name__)

    def _stop_service(self) -> None:
        """
        Ensures all spans are evaluated & evaluation metrics are submitted when evaluator runner
        is stopped by the LLM Obs instance
        """
        self.periodic(_wait_sync=True)
        if self._executor is not None:
            self._executor.shutdown(wait=True)

    def recreate(self) -> "EvaluatorRunner":
        return self.__class__(
            interval=self._interval,
            llmobs_service=self.llmobs_service,
            evaluators=self.evaluators,
        )

    def enqueue(self, span_event: LLMObsSpanEvent, span: Span) -> None:
        if self.status == ServiceStatus.STOPPED:
            return
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self._buffer_limit
                )
                return
            self._buffer.append((span_event, span))

    def periodic(self, _wait_sync: bool = False) -> None:
        """
        :param bool _wait_sync: if `True`, each evaluator is run for each span in the buffer
        synchronously. This param is only set to `True` for when the evaluator runner is stopped by the LLM Obs
        instance on process exit and we want to block until all spans are evaluated and metrics are submitted.
        """
        with self._lock:
            if not self._buffer:
                return
            span_events_and_spans = self._buffer
            self._buffer = []

        try:
            for evaluator in self.evaluators:
                for span_event, span in span_events_and_spans:
                    if self.sampler.sample(evaluator.LABEL, span):
                        if not _wait_sync:
                            if self._executor is None:
                                from concurrent.futures import ThreadPoolExecutor

                                self._executor = ThreadPoolExecutor()  # type: ignore[assignment]
                            self._executor.submit(evaluator.run_and_submit_evaluation, span_event)  # type: ignore[attr-defined]
                        else:
                            evaluator.run_and_submit_evaluation(span_event)
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)
