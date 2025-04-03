import json
import math
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import EVALUATION_KIND_METADATA
from ddtrace.llmobs._constants import EVALUATION_SPAN_METADATA
from ddtrace.llmobs._constants import FAITHFULNESS_DISAGREEMENTS_METADATA
from ddtrace.llmobs._constants import IS_EVALUATION_SPAN
from ddtrace.llmobs._evaluators.ragas.base import BaseRagasEvaluator
from ddtrace.llmobs._evaluators.ragas.base import _get_ml_app_for_ragas_trace


logger = get_logger(__name__)


class RagasFaithfulnessEvaluator(BaseRagasEvaluator):
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

        The `ragas.metrics.faithfulness` instance is used for faithfulness scores. If there is no llm attribute set
        on this instance, it will be set to the default `llm_factory()` which uses openai.

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                                      submitting evaluation metrics.

        Raises: NotImplementedError if the ragas library is not found or if ragas version is not supported.
        """
        super().__init__(llmobs_service)
        self.ragas_faithfulness_instance = self._get_faithfulness_instance()
        self.llm_output_parser_for_generated_statements = self.ragas_dependencies.RagasoutputParser(
            pydantic_object=self.ragas_dependencies.StatementsAnswers
        )
        self.llm_output_parser_for_faithfulness_score = self.ragas_dependencies.RagasoutputParser(
            pydantic_object=self.ragas_dependencies.StatementFaithfulnessAnswers
        )
        self.split_answer_into_sentences = self.ragas_dependencies.get_segmenter(
            language=self.ragas_faithfulness_instance.nli_statements_message.language, clean=False
        )

    def _get_faithfulness_instance(self) -> Optional[object]:
        """
        This helper function ensures the faithfulness instance used in
        ragas evaluator is updated with the latest ragas faithfulness
        instance AND has an non-null llm
        """
        if self.ragas_dependencies.faithfulness is None:
            return None
        ragas_faithfulness_instance = self.ragas_dependencies.faithfulness
        if not ragas_faithfulness_instance.llm:
            ragas_faithfulness_instance.llm = self.ragas_dependencies.llm_factory()
        return ragas_faithfulness_instance

    def evaluate(self, span_event: dict) -> Tuple[Union[float, str], Optional[dict]]:
        """
        Performs a faithfulness evaluation on a span event, returning either
            - faithfulness score (float) OR
            - failure reason (str)
        If the ragas faithfulness instance does not have `llm` set, we set `llm` using the `llm_factory()`
        method from ragas which defaults to openai's gpt-4o-turbo.
        """
        self.ragas_faithfulness_instance = self._get_faithfulness_instance()
        if not self.ragas_faithfulness_instance:
            return "fail_faithfulness_is_none", {}

        evaluation_metadata = {EVALUATION_KIND_METADATA: "faithfulness"}  # type: dict[str, Union[str, dict, list]]

        # initialize data we annotate for tracing ragas
        score, question, answer, context, statements, faithfulness_list = (
            math.nan,
            None,
            None,
            None,
            None,
            None,
        )

        with self.llmobs_service.workflow(
            "dd-ragas.faithfulness", ml_app=_get_ml_app_for_ragas_trace(span_event)
        ) as ragas_faithfulness_workflow:
            ragas_faithfulness_workflow._set_ctx_item(IS_EVALUATION_SPAN, True)
            try:
                evaluation_metadata[EVALUATION_SPAN_METADATA] = self.llmobs_service.export_span(
                    span=ragas_faithfulness_workflow
                )

                faithfulness_inputs = self._extract_evaluation_inputs_from_span(span_event)
                if faithfulness_inputs is None:
                    logger.debug(
                        "Failed to extract evaluation inputs from span sampled for `ragas_faithfulness` evaluation"
                    )
                    return "fail_extract_faithfulness_inputs", evaluation_metadata

                question = faithfulness_inputs["question"]
                answer = faithfulness_inputs["answer"]
                context = " ".join(faithfulness_inputs["contexts"])

                statements = self._create_statements(question, answer)
                if statements is None:
                    logger.debug("Failed to create statements from answer for `ragas_faithfulness` evaluator")
                    return "statements_is_none", evaluation_metadata

                faithfulness_list = self._create_verdicts(context, statements)
                if faithfulness_list is None:
                    logger.debug("Failed to create faithfulness list `ragas_faithfulness` evaluator")
                    return "statements_create_faithfulness_list", evaluation_metadata

                evaluation_metadata[FAITHFULNESS_DISAGREEMENTS_METADATA] = [
                    {"answer_quote": answer.statement} for answer in faithfulness_list.__root__ if answer.verdict == 0
                ]

                score = self._compute_score(faithfulness_list)
                if math.isnan(score):
                    logger.debug("Score computation returned NaN for `ragas_faithfulness` evaluator")
                    return "statements_compute_score", evaluation_metadata

                return score, evaluation_metadata
            finally:
                self.llmobs_service.annotate(
                    span=ragas_faithfulness_workflow,
                    input_data=span_event,
                    output_data=score,
                    metadata={
                        "statements": statements,
                        "faithfulness_list": faithfulness_list.dicts() if faithfulness_list is not None else None,
                    },
                )

    def _create_statements(self, question: str, answer: str) -> Optional[List[str]]:
        with self.llmobs_service.workflow("dd-ragas.create_statements"):
            self.llmobs_service.annotate(
                input_data={"question": question, "answer": answer},
            )
            statements_prompt = self._create_statements_prompt(answer=answer, question=question)

            """LLM step to break down the answer into simpler statements"""
            statements = self.ragas_faithfulness_instance.llm.generate_text(statements_prompt)

            statements = self.llm_output_parser_for_generated_statements.parse(statements.generations[0][0].text)

            if statements is None:
                return None
            statements = [item["simpler_statements"] for item in statements.dicts()]
            statements = [item for sublist in statements for item in sublist]

            self.llmobs_service.annotate(
                output_data=statements,
            )
            if not isinstance(statements, List):
                return None
            return statements

    def _create_verdicts(self, context: str, statements: List[str]):
        """
        Returns: `StatementFaithfulnessAnswers` model detailing which statements are faithful to the context
        """
        with self.llmobs_service.workflow("dd-ragas.create_verdicts") as create_verdicts_workflow:
            self.llmobs_service.annotate(
                span=create_verdicts_workflow,
                input_data=statements,
            )
            """Check which statements contradict the conntext"""
            raw_nli_results = self.ragas_faithfulness_instance.llm.generate_text(
                self._create_natural_language_inference_prompt(context, statements)
            )
            if len(raw_nli_results.generations) == 0:
                return None

            reproducibility = getattr(self.ragas_faithfulness_instance, "_reproducibility", 1)

            raw_nli_results_texts = [raw_nli_results.generations[0][i].text for i in range(reproducibility)]

            raw_faithfulness_list = [
                faith.dicts()
                for faith in [
                    self.llm_output_parser_for_faithfulness_score.parse(text) for text in raw_nli_results_texts
                ]
                if faith is not None
            ]

            if len(raw_faithfulness_list) == 0:
                return None

            # collapse multiple generations into a single faithfulness list
            faithfulness_list = self.ragas_dependencies.ensembler.from_discrete(raw_faithfulness_list, "verdict")
            try:
                return self.ragas_dependencies.StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
            except Exception as e:
                logger.debug("Failed to parse faithfulness_list", exc_info=e)
                return None
            finally:
                self.llmobs_service.annotate(
                    span=create_verdicts_workflow,
                    output_data=faithfulness_list,
                )

    def _create_statements_prompt(self, answer, question):
        # Returns: `ragas.llms.PromptValue` object
        with self.llmobs_service.task("dd-ragas.create_statements_prompt"):
            sentences = self.split_answer_into_sentences.segment(answer)
            sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
            sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
            return self.ragas_faithfulness_instance.statement_prompt.format(
                question=question, answer=answer, sentences=sentences
            )

    def _create_natural_language_inference_prompt(self, context_str: str, statements: List[str]):
        # Returns: `ragas.llms.PromptValue` object
        with self.llmobs_service.task("dd-ragas.create_natural_language_inference_prompt"):
            prompt_value = self.ragas_faithfulness_instance.nli_statements_message.format(
                context=context_str, statements=json.dumps(statements)
            )
            return prompt_value

    def _compute_score(self, faithfulness_list) -> float:
        """
        Args:
            faithfulness_list (StatementFaithfulnessAnswers): a list of statements and their faithfulness verdicts
        """
        with self.llmobs_service.task("dd-ragas.compute_score"):
            faithful_statements = sum(1 if answer.verdict else 0 for answer in faithfulness_list.__root__)
            num_statements = len(faithfulness_list.__root__)
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
