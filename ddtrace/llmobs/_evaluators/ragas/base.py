from abc import ABC
from abc import abstractmethod
import traceback
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import RAGAS_ML_APP_PREFIX


logger = get_logger(__name__)


class MiniRagas:
    """
    A helper class to store instances of ragas classes and functions
    that may or may not exist in a user's environment.
    """

    def __init__(self):
        import ragas

        self.ragas_version = parse_version(ragas.__version__)
        if self.ragas_version >= (0, 2, 0) or self.ragas_version < (0, 1, 10):
            raise NotImplementedError(
                "Ragas version: {} is not supported".format(self.ragas_version),
            )

        from ragas.llms import llm_factory

        self.llm_factory = llm_factory

        from ragas.llms.output_parser import RagasoutputParser

        self.RagasoutputParser = RagasoutputParser

        from ragas.metrics import context_precision

        self.context_precision = context_precision

        from ragas.metrics.base import ensembler

        self.ensembler = ensembler

        from ddtrace.llmobs._evaluators.ragas.models import ContextPrecisionVerification

        self.ContextPrecisionVerification = ContextPrecisionVerification


def _get_ml_app_for_ragas_trace(span_event: dict) -> str:
    """
    The `ml_app` spans generated from traces of ragas will be named as `dd-ragas-<ml_app>`
    or `dd-ragas` if `ml_app` is not present in the span event.
    """
    tags = span_event.get("tags", [])  # list[str]
    ml_app = None
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("ml_app:"):
            ml_app = tag.split(":")[1]
            break
    if not ml_app:
        return RAGAS_ML_APP_PREFIX
    return "{}-{}".format(RAGAS_ML_APP_PREFIX, ml_app)


class RagasBaseEvaluator(ABC):
    """A class used by EvaluatorRunner to conduct ragas evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.
    """

    LABEL = "ragas_context_precision"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        """
        Initialize an evaluator that uses the ragas library to generate a score on finished LLM spans.

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                                      submitting evaluation metrics.

        Raises: NotImplementedError if the ragas library is not found or if ragas version is not supported.
        """
        self.llmobs_service = llmobs_service
        self.ragas_version = "unknown"
        telemetry_state = "ok"
        try:
            self.mini_ragas = MiniRagas()
        except Exception as e:
            telemetry_state = "fail"
            telemetry_writer.add_log(
                level=TELEMETRY_LOG_LEVEL.ERROR,
                message="Failed to import Ragas dependencies",
                stack_trace=traceback.format_exc(),
                tags={"ragas_version": self.ragas_version},
            )
            raise NotImplementedError("Failed to load dependencies for `{}` evaluator".format(self.LABEL)) from e
        finally:
            telemetry_writer.add_count_metric(
                namespace=TELEMETRY_APM_PRODUCT.LLMOBS,
                name="evaluators.init",
                value=1,
                tags=(
                    ("evaluator_label", self.LABEL),
                    ("state", telemetry_state),
                    ("ragas_version", self.ragas_version),
                ),
            )

    def run_and_submit_evaluation(self, span_event: dict):
        if not span_event:
            return
        score_result_or_failure, metric_metadata = self.evaluate(span_event)
        telemetry_writer.add_count_metric(
            TELEMETRY_APM_PRODUCT.LLMOBS,
            "evaluators.run",
            1,
            tags=(
                ("evaluator_label", self.LABEL),
                ("state", score_result_or_failure if isinstance(score_result_or_failure, str) else "success"),
            ),
        )
        if isinstance(score_result_or_failure, float):
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value=score_result_or_failure,
                metadata=metric_metadata,
            )

    @abstractmethod
    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        pass
