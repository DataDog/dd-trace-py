import math
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import EVALUATION_KIND_METADATA
from ddtrace.llmobs._constants import EVALUATION_SPAN_METADATA
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._evaluators.ragas.base import BaseRagasEvaluator
from ddtrace.llmobs._evaluators.ragas.base import _get_ml_app_for_ragas_trace


logger = get_logger(__name__)


class RagasContextPrecisionEvaluator(BaseRagasEvaluator):
    """
    A class used by EvaluatorRunner to conduct ragas context precision evaluations
    on LLM Observability span events.
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
        super().__init__(llmobs_service)
        self.ragas_context_precision_instance = self._get_context_precision_instance()
        self.context_precision_output_parser = self.ragas_dependencies.RagasoutputParser(
            pydantic_object=self.ragas_dependencies.ContextPrecisionVerification
        )

    def _get_context_precision_instance(self):
        """
        This helper function ensures the context precision instance used in
        ragas evaluator is updated with the latest ragas context precision instance
        instance AND has an non-null llm
        """
        if self.ragas_dependencies.context_precision is None:
            return None
        ragas_context_precision_instance = self.ragas_dependencies.context_precision
        if not ragas_context_precision_instance.llm:
            ragas_context_precision_instance.llm = self.ragas_dependencies.llm_factory()
        return ragas_context_precision_instance

    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        """
        Performs a context precision evaluation on an llm span event, returning either
            - context precision score (float) OR failure reason (str)
            - evaluation metadata (dict)
        If the ragas context precision instance does not have `llm` set, we set `llm` using the `llm_factory()`
        method from ragas which currently defaults to openai's gpt-4o-turbo.
        """
        self.ragas_context_precision_instance = self._get_context_precision_instance()
        if not self.ragas_context_precision_instance:
            return "fail_context_precision_is_none", {}

        evaluation_metadata = {EVALUATION_KIND_METADATA: "context_precision"}  # type: dict[str, Union[str, dict, list]]

        # initialize data we annotate for tracing ragas
        score = math.nan

        with self.llmobs_service.workflow(
            "dd-ragas.context_precision", ml_app=_get_ml_app_for_ragas_trace(span_event)
        ) as ragas_cp_workflow:
            ragas_cp_workflow._set_ctx_item(IS_EVALUATION_SPAN, True)
            try:
                evaluation_metadata[EVALUATION_SPAN_METADATA] = self.llmobs_service.export_span(span=ragas_cp_workflow)

                ctx_precision_inputs = self._extract_evaluation_inputs_from_span(span_event)
                if ctx_precision_inputs is None:
                    logger.debug(
                        "Failed to extract evaluation inputs from "
                        "span sampled for `ragas_context_precision` evaluation"
                    )
                    return "fail_extract_context_precision_inputs", evaluation_metadata

                # create a prompt to evaluate the relevancy of each context chunk
                context_precision_prompts = [
                    self.ragas_context_precision_instance.context_precision_prompt.format(
                        question=ctx_precision_inputs["question"],
                        context=c,
                        answer=ctx_precision_inputs["answer"],
                    )
                    for c in ctx_precision_inputs["contexts"]
                ]

                responses = []

                for prompt in context_precision_prompts:
                    result = self.ragas_context_precision_instance.llm.generate_text(prompt)
                    reproducibility = getattr(self.ragas_context_precision_instance, "_reproducibility", 1)

                    results = [result.generations[0][i].text for i in range(reproducibility)]
                    try:
                        responses.append(
                            [
                                res.dict()
                                for res in [self.context_precision_output_parser.parse(text) for text in results]
                                if res is not None
                            ]
                        )
                    except Exception as e:
                        logger.debug(
                            "Failed to parse context precision verification for `ragas_context_precision`",
                            exc_info=e,
                        )
                        return "fail_context_precision_parsing", evaluation_metadata

                answers = []
                for response in responses:
                    agg_answer = self.ragas_dependencies.ensembler.from_discrete([response], "verdict")
                    if agg_answer:
                        try:
                            agg_answer = self.ragas_dependencies.ContextPrecisionVerification.parse_obj(agg_answer[0])
                        except Exception as e:
                            logger.debug(
                                "Failed to parse context precision verification for `ragas_context_precision`",
                                exc_info=e,
                            )
                            return "fail_context_precision_parsing", evaluation_metadata
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
