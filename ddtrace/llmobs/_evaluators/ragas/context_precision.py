import math
import traceback
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import EVALUATION_KIND_METADATA
from ddtrace.llmobs._constants import EVALUATION_SPAN_METADATA
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS
from ddtrace.llmobs._constants import RAGAS_ML_APP_PREFIX


logger = get_logger(__name__)


class MiniRagas:
    """
    A helper class to store instances of ragas classes and functions
    that may or may not exist in a user's environment.
    """

    llm_factory = None
    RagasoutputParser = None
    ensembler = None
    context_precision = None
    ContextPrecisionVerification = None


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


def _get_context_precision_instance():
    """
    This helper function ensures the context precision instance used in
    ragas evaluator is updated with the latest ragas context precision instance
    instance AND has an non-null llm
    """
    if MiniRagas.context_precision is None:
        return None
    ragas_context_precision_instance = MiniRagas.context_precision
    if not ragas_context_precision_instance.llm:
        ragas_context_precision_instance.llm = MiniRagas.llm_factory()
    return ragas_context_precision_instance


class RagasContextPrecisionEvaluator:
    """A class used by EvaluatorRunner to conduct ragas context precision evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.
    """

    LABEL = "ragas_context_precision"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        """
        Initialize an evaluator that uses the ragas library to generate a context precision score on finished LLM spans.

        Context Precision is a metric that verifies if the context was useful in arriving at the given answer.
        We compute this by dividing the number of relevant contexts by the total number of contexts.
        Note that this is slightly modified from the original context precision metric in ragas, which computes
        the mean of the precision @ rank k for each chunk in the context (where k is the number of
        retrieved context chunks).

        For more information, see https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/context_precision/

        The `ragas.metrics.context_precision` instance is used for context precision scores.
        If there is no llm attribute set on this instance, it will be set to the
        default `llm_factory()` which uses openai.

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                                      submitting evaluation metrics.

        Raises: NotImplementedError if the ragas library is not found or if ragas version is not supported.
        """
        self.llmobs_service = llmobs_service
        self.ragas_version = "unknown"
        telemetry_state = "ok"
        try:
            import ragas

            self.ragas_version = parse_version(ragas.__version__)
            if self.ragas_version >= (0, 2, 0) or self.ragas_version < (0, 1, 10):
                raise NotImplementedError(
                    "Ragas version: {} is not supported for `ragas_context_precision` evaluator".format(
                        self.ragas_version
                    ),
                )

            from ragas.llms import llm_factory

            MiniRagas.llm_factory = llm_factory

            from ragas.llms.output_parser import RagasoutputParser

            MiniRagas.RagasoutputParser = RagasoutputParser

            from ragas.metrics import context_precision

            MiniRagas.context_precision = context_precision

            from ragas.metrics.base import ensembler

            MiniRagas.ensembler = ensembler

            from ddtrace.llmobs._evaluators.ragas.models import ContextPrecisionVerification

            MiniRagas.ContextPrecisionVerification = ContextPrecisionVerification

        except Exception as e:
            telemetry_state = "fail"
            telemetry_writer.add_log(
                level=TELEMETRY_LOG_LEVEL.ERROR,
                message="Failed to import Ragas dependencies",
                stack_trace=traceback.format_exc(),
                tags={"ragas_version": self.ragas_version},
            )
            raise NotImplementedError("Failed to load dependencies for `ragas_context_precision` evaluator") from e
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

        self.ragas_context_precision_instance = _get_context_precision_instance()
        self.context_precision_output_parser = MiniRagas.RagasoutputParser(
            pydantic_object=MiniRagas.ContextPrecisionVerification
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

    def _extract_inputs(self, span_event: dict) -> Optional[dict]:
        """
        Extracts the question, answer, and context used as inputs to faithfulness
        evaluation from a span event.

        question - input.prompt.variables.question OR input.messages[-1].content
        context - input.prompt.variables.context
        answer - output.messages[-1].content
        """
        with self.llmobs_service.workflow("dd-ragas.extract_context_precision_inputs") as extract_inputs_workflow:
            self.llmobs_service.annotate(span=extract_inputs_workflow, input_data=span_event)
            question, answer, context = None, None, None

            meta_io = span_event.get("meta")
            if meta_io is None:
                return None

            meta_input = meta_io.get("input")
            meta_output = meta_io.get("output")

            if not (meta_input and meta_output):
                return None

            prompt = meta_input.get("prompt")
            if prompt is None:
                logger.debug("Failed to extract `prompt` from span for `ragas_faithfulness` evaluation")
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
                span=extract_inputs_workflow, output_data={"question": question, "context": context, "answer": answer}
            )
            if any(field is None for field in (question, contexts, answer)):
                logger.debug("Failed to extract inputs required for faithfulness evaluation")
                return None

            return {"question": question, "contexts": contexts, "answer": answer}

    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        """
        Performs a context precision evaluation on a retrieval span event, returning either
            - context precision score (float) OR failure reason (str)
            - evaluation metadata (dict)
        If the ragas context precision instance does not have `llm` set, we set `llm` using the `llm_factory()`
        method from ragas which currently defaults to openai's gpt-4o-turbo.
        """
        self.ragas_context_precision_instance = _get_context_precision_instance()
        if not self.ragas_context_precision_instance:
            return "fail_context_precision_is_none", {}

        evaluation_metadata = {EVALUATION_KIND_METADATA: "context_precision"}  # type: dict[str, Union[str, dict, list]]

        # initialize data we annotate for tracing ragas
        score, question, answer = (
            math.nan,
            None,
            None,
        )

        with self.llmobs_service.workflow(
            "dd-ragas.context_precision", ml_app=_get_ml_app_for_ragas_trace(span_event)
        ) as ragas_cp_workflow:
            try:
                evaluation_metadata[EVALUATION_SPAN_METADATA] = self.llmobs_service.export_span(span=ragas_cp_workflow)

                cp_inputs = self._extract_inputs(span_event)
                if cp_inputs is None:
                    logger.debug(
                        "Failed to extract question and contexts from "
                        "span sampled for `ragas_context_precision` evaluation"
                    )
                    return "fail_extract_context_precision_inputs", evaluation_metadata

                question = cp_inputs["question"]
                contexts = cp_inputs["contexts"]
                answer = cp_inputs["answer"]

                # create a prompt to evaluate each context chunk
                ctx_precision_prompts = [
                    self.ragas_context_precision_instance.context_precision_prompt.format(
                        question=question, context=c, answer=answer
                    )
                    for c in contexts
                ]

                responses = []

                for prompt in ctx_precision_prompts:
                    result = self.ragas_context_precision_instance.llm.generate_text(prompt)
                    reproducibility = getattr(self.ragas_context_precision_instance, "_reproducibility", 1)

                    results = [result.generations[0][i].text for i in range(reproducibility)]
                    responses.append(
                        [
                            res.dict()
                            for res in [self.context_precision_output_parser.parse(text) for text in results]
                            if res is not None
                        ]
                    )

                answers = []  # type: list[MiniRagas.ContextPrecisionVerification]
                for response in responses:
                    agg_answer = MiniRagas.ensembler.from_discrete([response], "verdict")
                    if agg_answer:
                        try:
                            agg_answer = MiniRagas.ContextPrecisionVerification.parse_obj(agg_answer[0])
                        except Exception as e:
                            logger.debug(
                                "Failed to parse context precision verification for `ragas_context_precision`",
                                exc_info=e,
                            )
                            continue
                    answers.append(agg_answer)

                if len(answers) == 0:
                    return "fail_no_answers", evaluation_metadata

                verdict_list = [1 if ver.verdict else 0 for ver in answers]
                score = sum(verdict_list) / len(verdict_list)
                return score, evaluation_metadata
            finally:
                self.llmobs_service.annotate(
                    span=ragas_cp_workflow,
                    input_data=span_event,
                    output_data=score,
                )
