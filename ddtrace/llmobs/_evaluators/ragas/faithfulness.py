import json
import math
import time
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import RUNNER_IS_INTEGRATION_SPAN_TAG
from ddtrace.llmobs._evaluators.ragas.utils import _get_ml_app_for_ragas_trace


logger = get_logger(__name__)

RAGAS_DEPENDENCIES_PRESENT = False
RAGAS = "ragas"

try:
    from ragas.llms import llm_factory
    from ragas.llms.output_parser import RagasoutputParser
    from ragas.metrics import faithfulness
    from ragas.metrics.base import ensembler
    from ragas.metrics.base import get_segmenter

    from ddtrace.llmobs._evaluators.ragas.utils import StatementFaithfulnessAnswers
    from ddtrace.llmobs._evaluators.ragas.utils import StatementsAnswers

    RAGAS_DEPENDENCIES_PRESENT = True
except ImportError as e:
    logger.warning("RagasFaithfulnessEvaluator is disabled because Ragas requirements are not installed", exc_info=e)


class RagasFaithfulnessEvaluator:
    """A class used by EvaluatorRunner to conduct ragas faithfulness evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.
    """

    LABEL = "ragas_faithfulness"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        """
        Initialize an evaluator that uses the ragas library to generate a faithfulness score on finished LLM spans.

        Faithfulness measures the factual consistency of an LLM's output against a given context.
        There are two LLM calls required to generate a faithfulness score - one to generate a set of statements from
        the answer, and another to measure the faithfulness of those statements against the context using natural
        language entailment.

        For more information, see https://docs.ragas.io/en/latest/concepts/metrics/faithfulness/

        The `ragas.metrics.faithfulness` instance is used for faithfulness scores. If there is not llm attribute set
        on this instance, it will be set to the default `llm_factory()` which uses openai.

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                                submitting evaluation metrics.
        """
        self.enabled = True
        if not RAGAS_DEPENDENCIES_PRESENT:
            self.enabled = False
            return

        self.llmobs_service = llmobs_service
        self.ragas_faithfulness_instance = faithfulness

        if not self.ragas_faithfulness_instance.llm:
            self.ragas_faithfulness_instance.llm = llm_factory()

        self.llm_output_parser_for_generated_statements = RagasoutputParser(pydantic_object=StatementsAnswers)

        self.llm_output_parser_for_faithfulness_score = RagasoutputParser(pydantic_object=StatementFaithfulnessAnswers)

        self.split_answer_into_sentences = get_segmenter(
            language=self.ragas_faithfulness_instance.nli_statements_message.language, clean=False
        )

    def run_and_submit_evaluation(self, span_event: dict):
        if not span_event:
            return
        score_result = self.evaluate(span_event)
        if score_result:
            self.llmobs_service.submit_evaluation(
                span_context=span_event,
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=score_result,
                timestamp_ms=math.floor(time.time() * 1000),
            )

    def evaluate(self, span_event: dict) -> Optional[float]:
        if not self.enabled:
            return None

        """Initialize defaults for variables we want to annotate on the LLM Observability trace of the
        ragas faithfulness evaluations."""
        score, question, answer, context, statements, faithfulness_list = math.nan, None, None, None, None, None

        with self.llmobs_service.annotation_context(
            tags={RUNNER_IS_INTEGRATION_SPAN_TAG: RAGAS}, ml_app=_get_ml_app_for_ragas_trace(span_event)
        ):
            with self.llmobs_service.workflow("ragas_faithfulness"):
                try:
                    faithfulness_inputs = self._extract_faithfulness_inputs(span_event)
                    if faithfulness_inputs is None:
                        logger.debug(
                            "Failed to extract question and context from span sampled for ragas_faithfulness evaluation"
                        )
                        return None

                    question = faithfulness_inputs["question"]
                    answer = faithfulness_inputs["answer"]
                    context = faithfulness_inputs["context"]

                    statements_prompt = self._create_statements_prompt(answer=answer, question=question)

                    statements = self.ragas_faithfulness_instance.llm.generate_text(statements_prompt)

                    statements = self.llm_output_parser_for_generated_statements.parse(
                        statements.generations[0][0].text
                    )

                    if statements is None:
                        return None
                    statements = [item["simpler_statements"] for item in statements.dicts()]
                    statements = [item for sublist in statements for item in sublist]

                    assert isinstance(statements, List), "statements must be a list"

                    raw_nli_results = self.ragas_faithfulness_instance.llm.generate_text(
                        self._create_nli_prompt(statements, context)
                    )
                    if len(raw_nli_results.generations) == 0:
                        return None

                    raw_nli_results_texts = [
                        raw_nli_results[0][i].text for i in range(self.ragas_faithfulness_instance._reproducibility)
                    ]

                    multiple_faithfulness_lists = [
                        faith.dicts()
                        for faith in [
                            self.llm_output_parser_for_faithfulness_score.parse(text) for text in raw_nli_results_texts
                        ]
                        if faith is not None
                    ]

                    if len(multiple_faithfulness_lists) == 0:
                        return None

                    faithfulness_list = ensembler.from_discrete(
                        multiple_faithfulness_lists,
                        "verdict",
                    )
                    try:
                        faithfulness_list = StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
                    except Exception as e:
                        logger.debug("Failed to parse faithfulness_list", exc_info=e)
                        return None

                    score = self._compute_score(faithfulness_list)

                    if math.isnan(score):
                        return None

                    return score
                finally:
                    self.llmobs_service.annotate(
                        input_data=span_event,
                        output_data=score,
                        metadata={
                            "statements": statements,
                            "faithfulness_list": faithfulness_list,
                        },
                    )

    def _extract_faithfulness_inputs(self, span_event: dict) -> Optional[dict]:
        with self.llmobs_service.workflow("ragas.extract_faithfulness_inputs"):
            self.llmobs_service.annotate(input_data=span_event)
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
                return None
            prompt_variables = prompt.get("variables")

            messages = meta_output.get("messages")
            if messages is not None and len(messages) > 0:
                answer = messages[-1].get("content")

            if prompt_variables:
                question = prompt_variables.get("question")
                context = prompt_variables.get("context")

            self.llmobs_service.annotate(output_data={"question": question, "context": context, "answer": answer})
            if any(field is None for field in (question, context, answer)):
                return None

            return {
                "question": question,
                "context": context,
                "answer": answer,
            }

    def _create_statements_prompt(self, answer, question):
        with self.llmobs_service.task("ragas.create_statements_prompt"):
            sentences = self.split_answer_into_sentences.segment(answer)
            sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
            sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
            return self.ragas_faithfulness_instance.statement_prompt.format(
                question=question, answer=answer, sentences=sentences
            )

    def _create_nli_prompt(self, statements, context_str):
        with self.llmobs_service.task("ragas.create_nli_prompt"):
            statements_str: str = json.dumps(statements)
            prompt_value = self.ragas_faithfulness_instance.nli_statements_message.format(
                context=context_str, statements=statements_str
            )
            return prompt_value

    def _compute_score(self, answers):
        with self.llmobs_service.task("ragas.compute_score"):
            faithful_statements = sum(1 if answer.verdict else 0 for answer in answers.__root__)
            num_statements = len(answers.__root__)
            if num_statements:
                score = faithful_statements / num_statements
            else:
                score = math.nan
            self.llmobs_service.annotate(
                metadata={
                    "faithful_statements": faithful_statements,
                    "num_statements": num_statements,
                },
                output_data=score,
            )
            return score
