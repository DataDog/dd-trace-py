import json
import math
import time
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import RUNNER_IS_INTEGRATION_SPAN_TAG


logger = get_logger(__name__)

RAGAS_DEPENDENCIES_PRESENT = None

try:
    from ragas.llms import llm_factory
    from ragas.llms.output_parser import RagasoutputParser
    from ragas.llms.output_parser import get_json_format_instructions
    from ragas.metrics import faithfulness
    from ragas.metrics.base import ensembler
    from ragas.metrics.base import get_segmenter

    from .utils import StatementFaithfulnessAnswers
    from .utils import StatementsAnswers

    RAGAS_DEPENDENCIES_PRESENT = True
except ImportError as e:
    logger.warning("RagasFaithfulnessEvaluator is disabled because Ragas requirements are not installed", exc_info=e)
    RAGAS_DEPENDENCIES_PRESENT = False


class RagasFaithfulnessEvaluator:
    """A class used by EvaluatorRunner to conduct ragas faithfulness evaluations
    on LLM Observability span events. The job of an Evaluator is to take a span and
    submit evaluation metrics based on the span's attributes.
    """

    LABEL = "ragas_faithfulness"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        self.enabled = True
        if not RAGAS_DEPENDENCIES_PRESENT:
            self.enabled = False
            return

        self.llmobs = llmobs_service
        self.faithfulness = faithfulness
        if not self.faithfulness.llm:
            self.faithfulness.llm = llm_factory()

        self.statements_output_instructions = get_json_format_instructions(StatementsAnswers)
        self.statements_output_parser = RagasoutputParser(pydantic_object=StatementsAnswers)
        self.faithfulness_output_instructions = get_json_format_instructions(StatementFaithfulnessAnswers)
        self.faithfulness_output_parser = RagasoutputParser(pydantic_object=StatementFaithfulnessAnswers)
        self.sentence_segmenter = get_segmenter(language=self.faithfulness.nli_statements_message.language, clean=False)

    def run_and_submit_evaluation(self, span):
        if not span:
            return
        score_result = self.evaluate(span)
        if score_result:
            self.llmobs_service.submit_evaluation(
                span_context=span,
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=score_result,
                timestamp_ms=math.floor(time.time() * 1000),
            )

    def evaluate(self, span) -> t.Optional[float]:
        if not self.enabled:
            return None
        llmobs_metadata = {}
        score = math.nan
        LLMObs = self.llmobs

        question, answer, context = None, None, None

        with LLMObs.annotation_context(tags={RUNNER_IS_INTEGRATION_SPAN_TAG: "ragas"}):
            with LLMObs.workflow("ragas_faithfulness") as workflow:
                try:
                    workflow.service = "ragas"

                    faithfulness_inputs = self._extract_faithfulness_inputs(span)
                    if faithfulness_inputs is None:
                        logger.debug(
                            "Failed to extract question and context from span sampled for ragas_faithfulness evaluation"
                        )
                        return None

                    question, answer, context = (
                        faithfulness_inputs["question"],
                        faithfulness_inputs["answer"],
                        faithfulness_inputs["context"],
                    )

                    statements_prompt = self._create_statements_prompt(answer=answer, question=question)

                    statements = self.faithfulness.llm.generate_text(statements_prompt)

                    statements = self.statements_output_parser.parse(statements.generations[0][0].text)

                    if statements is None:
                        return None
                    statements = [item["simpler_statements"] for item in statements.dicts()]
                    statements = [item for sublist in statements for item in sublist]

                    llmobs_metadata["statements"] = statements

                    assert isinstance(statements, t.List), "statements must be a list"

                    p_value = self._create_nli_prompt(statements, context)

                    nli_result = self.faithfulness.llm.generate_text(p_value)

                    nli_result_text = [
                        nli_result.generations[0][i].text for i in range(self.faithfulness._reproducibility)
                    ]
                    faithfulness_list = [self.faithfulness_output_parser.parse(text) for text in nli_result_text]

                    faithfulness_list = [faith.dicts() for faith in faithfulness_list if faith is not None]

                    llmobs_metadata["faithfulness_list"] = faithfulness_list

                    if faithfulness_list:
                        faithfulness_list = ensembler.from_discrete(
                            faithfulness_list,
                            "verdict",
                        )
                        try:
                            faithfulness_list = StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
                        except Exception as e:
                            logger.debug("Failed to parse faithfulness_list", exc_info=e)
                            return None
                    else:
                        return None

                    score = self._compute_score(faithfulness_list)

                    if math.isnan(score):
                        return None

                    return score
                finally:
                    LLMObs.annotate(
                        input_data=span,
                        output_data=score,
                        metadata=llmobs_metadata,
                    )

    def _extract_faithfulness_inputs(self, span: dict) -> t.Optional[dict]:
        with self.llmobs.workflow("ragas.extract_faithfulness_inputs"):
            self.llmobs.annotate(input_data=span)
            question, answer, context = None, None, None

            meta_io = span.get("meta")
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

            self.llmobs.annotate(output_data={"question": question, "context": context, "answer": answer})
            if question is None or context is None or answer is None:
                return None

            return {
                "question": question,
                "context": context,
                "answer": answer,
            }

    def _create_statements_prompt(self, answer, question):
        with self.llmobs.task("ragas.create_statements_prompt") as task:
            task.service = "ragas"
            sentences = self.sentence_segmenter.segment(answer)
            sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
            sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
            return self.faithfulness.statement_prompt.format(question=question, answer=answer, sentences=sentences)

    def _create_nli_prompt(self, statements, context_str):
        with self.llmobs.task("ragas.create_nli_prompt") as task:
            task.service = "ragas"
            statements_str: str = json.dumps(statements)
            prompt_value = self.faithfulness.nli_statements_message.format(
                context=context_str, statements=statements_str
            )
            return prompt_value

    def _compute_score(self, answers):
        with self.llmobs.task("ragas.compute_score") as task:
            task.service = "ragas"
            faithful_statements = sum(1 if answer.verdict else 0 for answer in answers.__root__)
            num_statements = len(answers.__root__)
            if num_statements:
                score = faithful_statements / num_statements
            else:
                score = math.nan
            self.llmobs.annotate(
                metadata={
                    "faithful_statements": faithful_statements,
                    "num_statements": num_statements,
                },
                output_data=score,
            )
            return score
