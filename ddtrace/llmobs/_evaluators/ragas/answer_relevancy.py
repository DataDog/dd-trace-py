import math
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import EVALUATION_KIND_METADATA
from ddtrace.llmobs._constants import EVALUATION_SPAN_METADATA
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS
from ddtrace.llmobs._evaluators.ragas.base import RagasBaseEvaluator
from ddtrace.llmobs._evaluators.ragas.base import _get_ml_app_for_ragas_trace


logger = get_logger(__name__)


class RagasAnswerRelevancyEvaluator(RagasBaseEvaluator):
    """A class used by EvaluatorRunner to conduct ragas answer relevancy evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.
    """

    LABEL = "ragas_context_precision"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        """
        Initialize an evaluator that uses the ragas library to generate a context precision score on finished LLM spans.

        ResponseRelevancy metric focuses on assessing how pertinent the generated answer is to a given question.
        A lower score is assigned to answers that are incomplete or contain redundant information and higher scores
        indicate better relevancy. This metric is computed using the question, contexts, and answer.

        For more information, see https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/answer_relevance/

        The `ragas.metrics.answer_relevancy` instance is used for context precision scores.
        If there is no llm attribute set on this instance, it will be set to the
        default `llm_factory()` which uses openai.

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                                      submitting evaluation metrics.

        Raises: NotImplementedError if the ragas library is not found or if ragas version is not supported.
        """
        super().__init__(llmobs_service)
        self.ragas_answer_relevancy_instance = self._get_answer_relevancy_instance()
        self.context_precision_output_parser = self.mini_ragas.RagasoutputParser(
            pydantic_object=self.mini_ragas.ContextPrecisionVerification
        )

    def _get_answer_relevancy_instance(self):
        """
        This helper function ensures the answer relevancy instance used in
        ragas evaluator is updated with the latest ragas answer relevancy instance
        instance AND has an non-null llm
        """
        if self.mini_ragas.answer_relevancy is None:
            return None
        ragas_answer_relevancy_instance = self.mini_ragas.answer_relevancy
        if not ragas_answer_relevancy_instance.llm:
            ragas_answer_relevancy_instance.llm = self.mini_ragas.llm_factory()
        return ragas_answer_relevancy_instance

    def _extract_inputs(self, span_event: dict) -> Optional[dict]:
        """
        Extracts the question, answer, and context used as inputs to a answer relevancy
        evaluation from a span event.

        question - input.prompt.variables.question OR input.messages[-1].content
        contexts - list of context prompt variables specified by
                        `input.prompt._dd_context_variable_keys` or defaults to `input.prompt.variables.context`
        answer - output.messages[-1].content
        """
        with self.llmobs_service.workflow("dd-ragas.extract_answer_relevancy_inputs") as extract_inputs_workflow:
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
                span=extract_inputs_workflow, output_data={"question": question, "contexts": contexts, "answer": answer}
            )
            if any(field is None for field in (question, contexts, answer)):
                logger.debug("Failed to extract inputs required for faithfulness evaluation")
                return None

            return {"question": question, "contexts": contexts, "answer": answer}

    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        """
        Performs a answer relevancy evaluation on an llm span event, returning either
            - answer relevancy score (float) OR failure reason (str)
            - evaluation metadata (dict)
        If the ragas answer relevancy instance does not have `llm` set, we set `llm` using the `llm_factory()`
        method from ragas which currently defaults to openai's gpt-4o-turbo.
        """
        self.ragas_context_precision_instance = self._get_context_precision_instance()
        if not self.ragas_context_precision_instance:
            return "fail_answer_relevancy_is_none", {}

        evaluation_metadata = {}  # type: dict[str, Union[str, dict, list]]

        # initialize data we annotate for tracing ragas
        score, question, answer = (
            math.nan,
            None,
            None,
        )

        with self.llmobs_service.workflow(
            "dd-ragas.answer_relevancy", ml_app=_get_ml_app_for_ragas_trace(span_event)
        ) as ragas_cp_workflow:
            try:
                evaluation_metadata[EVALUATION_SPAN_METADATA] = self.llmobs_service.export_span(span=ragas_cp_workflow)

                cp_inputs = self._extract_inputs(span_event)
                if cp_inputs is None:
                    logger.debug(
                        "Failed to extract question and contexts from "
                        "span sampled for `ragas_answer_relevancy` evaluation"
                    )
                    return "fail_extract_answer_relevancy_inputs", evaluation_metadata

                question = cp_inputs["question"]
                contexts = cp_inputs["context"]
                answer = cp_inputs["answer"]

                prompt = self.ragas_answer_relevancy_instance.question_generation.format(
                    answer=answer,
                    context="\n".join(contexts),
                )

                result = self.ragas_answer_relevancy_instance.llm.generate_text(
                    prompt, n=self.ragas_answer_relevancy_instance.strictness
                )

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

                answers = []
                for response in responses:
                    agg_answer = self.mini_ragas.ensembler.from_discrete([response], "verdict")
                    if agg_answer:
                        try:
                            agg_answer = self.mini_ragas.ContextPrecisionVerification.parse_obj(agg_answer[0])
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
