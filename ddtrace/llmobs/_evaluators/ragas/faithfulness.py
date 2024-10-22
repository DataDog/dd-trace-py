import json
import math
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import RAGAS_ML_APP_PREFIX


logger = get_logger(__name__)

RAGAS_DEPENDENCIES_PRESENT = False
_ragas_import_error_telemetry_log = ""
try:
    import ragas

    if parse_version(ragas.__version__) >= (0, 2, 0):
        logger.warning("Ragas version: {} is not supported for ragas_faithfulness", ragas.__version__)
        raise ValueError

    from ragas.llms import llm_factory
    from ragas.llms.output_parser import RagasoutputParser
    from ragas.metrics import faithfulness
    from ragas.metrics.base import ensembler
    from ragas.metrics.base import get_segmenter

    from ddtrace.llmobs._evaluators.ragas.models import StatementFaithfulnessAnswers
    from ddtrace.llmobs._evaluators.ragas.models import StatementsAnswers

    RAGAS_DEPENDENCIES_PRESENT = True
except Exception as e:
    _ragas_import_error_telemetry_log = str(e)
    logger.warning("Failed to import Ragas dependencies, not enabling ragas_faithfulness evaluator", exc_info=e)


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


def _get_faithfulness_instance():
    """
    This helper function ensures the faithfulness instance used in
    ragas evaluator is updated with the latest ragas faithfulness
    instance AND has an non-null llm
    """
    ragas_faithfulness_instance = faithfulness
    if not ragas_faithfulness_instance.llm:
        ragas_faithfulness_instance.llm = llm_factory()
    return ragas_faithfulness_instance


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

        The `ragas.metrics.faithfulness` instance is used for faithfulness scores. If there is no llm attribute set
        on this instance, it will be set to the default `llm_factory()` which uses openai.

        :param llmobs_service: An instance of the LLM Observability service used for tracing the evaluation and
                          cccccbgfdveevingkfhkkbevjvchlltguucbbjvtrgbk
                                      submitting evaluation metrics.
        """
        self.ragas_dependencies_present = RAGAS_DEPENDENCIES_PRESENT
        telemetry_writer.add_integration(
            "ragas_faithfulness",
            patched=self.ragas_dependencies_present,
            error_msg=_ragas_import_error_telemetry_log if not self.ragas_dependencies_present else None,
        )
        if not self.ragas_dependencies_present:
            return

        self.llmobs_service = llmobs_service
        self.ragas_faithfulness_instance = _get_faithfulness_instance()

        self.llm_output_parser_for_generated_statements = RagasoutputParser(pydantic_object=StatementsAnswers)

        self.llm_output_parser_for_faithfulness_score = RagasoutputParser(pydantic_object=StatementFaithfulnessAnswers)

        self.split_answer_into_sentences = get_segmenter(
            language=self.ragas_faithfulness_instance.nli_statements_message.language, clean=False
        )

    def run_and_submit_evaluation(self, span_event: dict):
        if not span_event:
            return
        score_result = self.evaluate(span_event)
        telemetry_writer.add_count_metric(
            "llmobs",
            "evaluators.ragas_faithfulness_run",
            1,
            tags=(("success", "true" if score_result is not None else "false"),),
        )
        if score_result is not None:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=score_result,
            )

    def evaluate(self, span_event: dict) -> Optional[float]:
        """
        Performs a faithfulness evaluation on a span event, returning the faithfulness score.
        If the ragas faithfulness instance does not have `llm` set, we set `llm` using the `llm_factory()`
        method from ragas which defaults to openai's gpt-4o-turbo.
        """
        if not self.ragas_dependencies_present:
            return None

        self.ragas_faithfulness_instance = _get_faithfulness_instance()

        score, question, answer, context, statements, faithfulness_list = math.nan, None, None, None, None, None

        with self.llmobs_service.workflow(
            "ragas.faithfulness", ml_app=_get_ml_app_for_ragas_trace(span_event)
        ) as ragas_faithfulness_workflow:
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

                statements = self._create_statements(question, answer)
                if statements is None:
                    logger.debug("Failed to create statements from answer for `ragas_faithfulness` evaluator")
                    return None

                faithfulness_list = self._create_verdicts(context, statements)
                if faithfulness_list is None:
                    logger.debug("Failed to create faithfulness list `ragas_faithfulness` evaluator")
                    return None

                score = self._compute_score(faithfulness_list)
                if math.isnan(score):
                    logger.debug("Score computation returned NaN for `ragas_faithfulness` evaluator")
                    return None

                return score
            finally:
                self.llmobs_service.annotate(
                    span=ragas_faithfulness_workflow,
                    input_data=span_event,
                    output_data=score,
                    metadata={
                        "statements": statements,
                        "faithfulness_list": faithfulness_list.dicts() if faithfulness_list else None,
                    },
                )

    def _create_statements(self, question: str, answer: str) -> Optional[List[str]]:
        with self.llmobs_service.workflow("ragas.create_statements"):
            statements_prompt = self._create_statements_prompt(answer=answer, question=question)

            """LLM step to break down the answer into simpler statements"""
            statements = self.ragas_faithfulness_instance.llm.generate_text(statements_prompt)

            statements = self.llm_output_parser_for_generated_statements.parse(statements.generations[0][0].text)

            if statements is None:
                return None
            statements = [item["simpler_statements"] for item in statements.dicts()]
            statements = [item for sublist in statements for item in sublist]

            if not isinstance(statements, List):
                return None
            return statements

    def _create_verdicts(self, context: str, statements: List[str]) -> Optional["StatementFaithfulnessAnswers"]:
        with self.llmobs_service.workflow("ragas.create_verdicts"):
            """Check which statements contradict the conntext"""
            raw_nli_results = self.ragas_faithfulness_instance.llm.generate_text(
                self._create_natural_language_inference_prompt(statements, context)
            )
            if len(raw_nli_results.generations) == 0:
                return None

            reproducibility = (
                self.ragas_faithfulness_instance._reproducibility
                if hasattr(self.ragas_faithfulness_instance, "_reproducibility")
                else 1
            )

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
            faithfulness_list = ensembler.from_discrete(
                raw_faithfulness_list,
                "verdict",
            )
            try:
                return StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
            except Exception as e:
                logger.debug("Failed to parse faithfulness_list", exc_info=e)
                return None

    def _extract_faithfulness_inputs(self, span_event: dict) -> Optional[dict]:
        """
        Extracts the question, answer, and context used as inputs to faithfulness
        evaluation from a span event.

        question - input.prompt.variables.question OR input.messages[-1].content
        context - input.prompt.variables.context
        answer - output.messages[-1].content
        """
        with self.llmobs_service.workflow("ragas.extract_faithfulness_inputs") as extract_inputs_workflow:
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
                return None
            prompt_variables = prompt.get("variables")

            input_messages = meta_input.get("messages")

            messages = meta_output.get("messages")
            if messages is not None and len(messages) > 0:
                answer = messages[-1].get("content")

            if prompt_variables:
                question = prompt_variables.get("question")
                context = prompt_variables.get("context")

            if not question and len(input_messages) > 0:
                question = input_messages[-1].get("content")

            self.llmobs_service.annotate(
                span=extract_inputs_workflow, output_data={"question": question, "context": context, "answer": answer}
            )
            if any(field is None for field in (question, context, answer)):
                logger.debug("Failed to extract inputs required for faithfulness evaluation")
                return None

            return {"question": question, "context": context, "answer": answer}

    def _create_statements_prompt(self, answer, question):
        with self.llmobs_service.task("ragas.create_statements_prompt"):
            sentences = self.split_answer_into_sentences.segment(answer)
            sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
            sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
            return self.ragas_faithfulness_instance.statement_prompt.format(
                question=question, answer=answer, sentences=sentences
            )

    def _create_natural_language_inference_prompt(self, statements, context_str):
        with self.llmobs_service.task("ragas.create_natural_language_inference_prompt"):
            statements_str: str = json.dumps(statements)
            prompt_value = self.ragas_faithfulness_instance.nli_statements_message.format(
                context=context_str, statements=statements_str
            )
            return prompt_value

    def _compute_score(self, faithfulness_list: "StatementFaithfulnessAnswers") -> float:
        with self.llmobs_service.task("ragas.compute_score"):
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
