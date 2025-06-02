import traceback
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS


logger = get_logger(__name__)


class RagasDependencies:
    """
    A helper class to store instances of ragas classes and functions
    that may or may not exist in a user's environment.
    """

    def __init__(self) -> None:
        import ragas

        self.ragas_version: str = ragas.__version__

        parsed_version = parse_version(ragas.__version__)
        if parsed_version >= (0, 2, 0) or parsed_version < (0, 1, 10):
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

        from ragas.metrics import faithfulness

        self.faithfulness = faithfulness

        from ragas.metrics.base import get_segmenter

        self.get_segmenter = get_segmenter

        from ragas.metrics import answer_relevancy

        self.answer_relevancy = answer_relevancy

        from ragas.embeddings import embedding_factory

        self.embedding_factory = embedding_factory

        from ddtrace.llmobs._evaluators.ragas.models import ContextPrecisionVerification

        self.ContextPrecisionVerification = ContextPrecisionVerification

        from ddtrace.llmobs._evaluators.ragas.models import StatementFaithfulnessAnswers

        self.StatementFaithfulnessAnswers = StatementFaithfulnessAnswers

        from ddtrace.llmobs._evaluators.ragas.models import StatementsAnswers

        self.StatementsAnswers = StatementsAnswers

        from ddtrace.llmobs._evaluators.ragas.models import AnswerRelevanceClassification

        self.AnswerRelevanceClassification = AnswerRelevanceClassification


def _get_ml_app_for_ragas_trace(span_event: dict) -> str:
    """
    The `ml_app` spans generated from traces of ragas will be named as `dd-ragas-<ml_app>`
    or `dd-ragas` if `ml_app` is not present in the span event.
    """
    tags: List[str] = span_event.get("tags", [])
    ml_app = None
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("ml_app:"):
            ml_app = tag.split(":")[1]
            break
    return ml_app or config._llmobs_ml_app or "unknown-ml-app"


class BaseRagasEvaluator:
    """A class used by EvaluatorRunner to conduct ragas evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.

    Extenders of this class should only need to implement the `evaluate` method.
    """

    LABEL = "ragas"
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
            self.ragas_dependencies = RagasDependencies()
            self.ragas_version = self.ragas_dependencies.ragas_version
        except ImportError as e:
            telemetry_state = "fail_import_error"
            raise NotImplementedError("Failed to load dependencies for `{}` evaluator".format(self.LABEL)) from e
        except AttributeError as e:
            telemetry_state = "fail_attribute_error"
            raise NotImplementedError("Failed to load dependencies for `{}` evaluator".format(self.LABEL)) from e
        except NotImplementedError as e:
            telemetry_state = "fail_not_supported"
            raise NotImplementedError("Failed to load dependencies for `{}` evaluator".format(self.LABEL)) from e
        except Exception as e:
            telemetry_state = "fail_unknown"
            raise NotImplementedError("Failed to load dependencies for `{}` evaluator".format(self.LABEL)) from e
        finally:
            telemetry_writer.add_count_metric(
                namespace=TELEMETRY_NAMESPACE.MLOBS,
                name="evaluators.init",
                value=1,
                tags=(
                    ("evaluator_label", self.LABEL),
                    ("state", telemetry_state),
                    ("evaluator_version", self.ragas_version),
                ),
            )
            if telemetry_state != "ok":
                telemetry_writer.add_log(
                    level=TELEMETRY_LOG_LEVEL.ERROR,
                    message="Failed to import Ragas dependencies",
                    stack_trace=traceback.format_exc(),
                    tags={"evaluator_version": self.ragas_version},
                )

    def run_and_submit_evaluation(self, span_event: dict):
        if not span_event:
            return
        score_result_or_failure, metric_metadata = self.evaluate(span_event)
        telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.MLOBS,
            "evaluators.run",
            1,
            tags=(
                ("evaluator_label", self.LABEL),
                ("state", score_result_or_failure if isinstance(score_result_or_failure, str) else "success"),
                ("evaluator_version", self.ragas_version),
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

    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        raise NotImplementedError("evaluate method must be implemented by individual evaluators")

    def _extract_evaluation_inputs_from_span(self, span_event: dict) -> Optional[dict]:
        """
        Extracts the question, answer, and context used as inputs for a ragas evaluation on a span event.
        """
        with self.llmobs_service.workflow("dd-ragas.extract_evaluation_inputs_from_span") as extract_inputs_workflow:
            self.llmobs_service.annotate(span=extract_inputs_workflow, input_data=span_event)
            question, answer, contexts = None, None, None

            meta_io = span_event.get("meta")
            if meta_io is None:
                return None

            meta_input = meta_io.get("input")
            meta_output = meta_io.get("output")

            if not (meta_input and meta_output):
                return None

            prompt = meta_input.get("prompt")
            if prompt is None:
                logger.debug("Failed to extract `prompt` from span for ragas evaluation")
                return None
            prompt_variables = prompt.get("variables")

            input_messages = meta_input.get("messages")

            messages = meta_output.get("messages")
            if messages is not None and len(messages) > 0:
                answer = messages[-1].get("content")

            if prompt_variables:
                context_keys = prompt.get(INTERNAL_CONTEXT_VARIABLE_KEYS, ["context"])
                question_keys = prompt.get(INTERNAL_QUERY_VARIABLE_KEYS, ["question"])
                contexts = [prompt_variables.get(key) for key in context_keys if prompt_variables.get(key)]
                question = " ".join([prompt_variables.get(key) for key in question_keys if prompt_variables.get(key)])

            if not question and input_messages is not None and len(input_messages) > 0:
                question = input_messages[-1].get("content")

            self.llmobs_service.annotate(
                span=extract_inputs_workflow, output_data={"question": question, "contexts": contexts, "answer": answer}
            )
            if any(field is None for field in (question, contexts, answer)):
                logger.debug("Failed to extract inputs required for ragas evaluation")
                return None

            return {"question": question, "contexts": contexts, "answer": answer}
