import math
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import EVALUATION_SPAN_METADATA
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._evaluators.ragas.base import BaseRagasEvaluator
from ddtrace.llmobs._evaluators.ragas.base import _get_ml_app_for_ragas_trace


logger = get_logger(__name__)


class RagasAnswerRelevancyEvaluator(BaseRagasEvaluator):
    """A class used by EvaluatorRunner to conduct ragas answer relevancy evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.
    """

    LABEL = "ragas_answer_relevancy"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        """
        Initialize an evaluator that uses the ragas library to generate a context precision score on finished LLM spans.

        answer relevancy focuses on assessing how pertinent the generated answer is to a given question.
        A lower score is assigned to answers that are incomplete or contain redundant information and higher scores
        indicate better relevancy. This metric is computed using the question, contexts, and answer.

        For more information, see https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/answer_relevance/

        The `ragas.metrics.answer_relevancy` instance is used for answer relevancy scores.
        If there is no llm attribute set on this instance, it will be set to the
        default `llm_factory()` from ragas which uses openai.
        If there is no embedding attribute set on this instance, it will be to to the
        default `embedding_factory()` from ragas which uses openai

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                                      submitting evaluation metrics.

        Raises: NotImplementedError if the ragas library is not found or if ragas version is not supported.
        """
        super().__init__(llmobs_service)
        self.ragas_answer_relevancy_instance = self._get_answer_relevancy_instance()
        self.answer_relevancy_output_parser = self.ragas_dependencies.RagasoutputParser(
            pydantic_object=self.ragas_dependencies.AnswerRelevanceClassification
        )

    def _get_answer_relevancy_instance(self):
        """
        This helper function ensures the answer relevancy instance used in
        ragas evaluator is updated with the latest ragas answer relevancy instance
        instance AND has an non-null llm
        """
        if self.ragas_dependencies.answer_relevancy is None:
            return None
        ragas_answer_relevancy_instance = self.ragas_dependencies.answer_relevancy
        if not ragas_answer_relevancy_instance.llm:
            ragas_answer_relevancy_instance.llm = self.ragas_dependencies.llm_factory()
        if not ragas_answer_relevancy_instance.embeddings:
            ragas_answer_relevancy_instance.embeddings = self.ragas_dependencies.embedding_factory()
        return ragas_answer_relevancy_instance

    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        """
        Performs a answer relevancy evaluation on an llm span event, returning either
            - answer relevancy score (float) OR failure reason (str)
            - evaluation metadata (dict)
        If the ragas answer relevancy instance does not have `llm` set, we set `llm` using the `llm_factory()`
        method from ragas which currently defaults to openai's gpt-4o-turbo.
        """
        self.ragas_answer_relevancy_instance = self._get_answer_relevancy_instance()
        if not self.ragas_answer_relevancy_instance:
            return "fail_answer_relevancy_is_none", {}

        evaluation_metadata = {}  # type: dict[str, Union[str, dict, list]]
        trace_metadata = {}  # type: dict[str, Union[str, dict, list]]

        # initialize data we annotate for tracing ragas
        score, answer_classifications = (math.nan, None)

        with self.llmobs_service.workflow(
            "dd-ragas.answer_relevancy", ml_app=_get_ml_app_for_ragas_trace(span_event)
        ) as ragas_ar_workflow:
            ragas_ar_workflow._set_ctx_item(IS_EVALUATION_SPAN, True)
            try:
                evaluation_metadata[EVALUATION_SPAN_METADATA] = self.llmobs_service.export_span(span=ragas_ar_workflow)

                answer_relevancy_inputs = self._extract_evaluation_inputs_from_span(span_event)
                if answer_relevancy_inputs is None:
                    logger.debug(
                        "Failed to extract question and contexts from "
                        "span sampled for `ragas_answer_relevancy` evaluation"
                    )
                    return "fail_extract_answer_relevancy_inputs", evaluation_metadata

                prompt = self.ragas_answer_relevancy_instance.question_generation.format(
                    answer=answer_relevancy_inputs["answer"],
                    context="\n".join(answer_relevancy_inputs["contexts"]),
                )

                trace_metadata["strictness"] = self.ragas_answer_relevancy_instance.strictness
                result = self.ragas_answer_relevancy_instance.llm.generate_text(
                    prompt, n=self.ragas_answer_relevancy_instance.strictness
                )

                try:
                    answers = [self.answer_relevancy_output_parser.parse(res.text) for res in result.generations[0]]
                    answers = [answer for answer in answers if answer is not None]
                except Exception as e:
                    logger.debug("Failed to parse answer relevancy output: %s", e)
                    return "fail_parse_answer_relevancy_output", evaluation_metadata

                gen_questions = [answer.question for answer in answers]
                answer_classifications = [
                    {"question": answer.question, "noncommittal": answer.noncommittal} for answer in answers
                ]
                trace_metadata["answer_classifications"] = answer_classifications
                if all(q == "" for q in gen_questions):
                    logger.warning("Invalid JSON response. Expected dictionary with key 'question'")
                    return "fail_parse_answer_relevancy_output", evaluation_metadata

                # calculate cosine similarity between the question and generated questions
                with self.llmobs_service.workflow("dd-ragas.calculate_similarity") as ragas_cs_workflow:
                    cosine_sim = self.ragas_answer_relevancy_instance.calculate_similarity(
                        answer_relevancy_inputs["question"], gen_questions
                    )
                    self.llmobs_service.annotate(
                        span=ragas_cs_workflow,
                        input_data={
                            "question": answer_relevancy_inputs["question"],
                            "generated_questions": gen_questions,
                        },
                        output_data=cosine_sim.mean(),
                    )

                score = cosine_sim.mean() * int(not any(answer.noncommittal for answer in answers))
                return score, evaluation_metadata
            finally:
                self.llmobs_service.annotate(
                    span=ragas_ar_workflow,
                    input_data=span_event,
                    output_data=score,
                    metadata=trace_metadata,
                )
