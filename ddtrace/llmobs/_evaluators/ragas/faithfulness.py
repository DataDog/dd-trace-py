import json
import math
import time
import typing as t

import ragas.faithfulness

import ddtrace
from ddtrace import config
from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)

RAGAS_DEPENDENCIES_PRESENT = None

try:
    from langchain_core.pydantic_v1.error_wrappers import ValidationError
    import np
    import ragas

    from .utils import FaithfulnessInputs
    from .utils import StatementFaithfulnessAnswers
except ImportError:
    logger.warning("RagasFaithfulnessEvaluator is disabled because Ragas requirements are not installed")
    RAGAS_DEPENDENCIES_PRESENT = False


class RagasFaithfulnessEvaluator:
    LABEL = "ragas_faithfulness"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        if not RAGAS_DEPENDENCIES_PRESENT:
            self.enabled = False
            return

        self.llmobs = llmobs_service
        self.faithfulness = ragas.faithfulness
        self.faithfulness.llm = ragas.llms.llm_factory()
        self.statement_prompt = self.faithfulness.statement_prompt
        self.statements_output_instructions = ragas.get_json_format_instructions(ragas.StatementsAnswers)
        self.statements_output_parser = ragas.RagasoutputParser(pydantic_object=ragas.StatementsAnswers)
        self.faithfulness_output_instructions = ragas.get_json_format_instructions(ragas.StatementFaithfulnessAnswers)
        self.faithfulness_output_parser = ragas.RagasoutputParser(pydantic_object=ragas.StatementFaithfulnessAnswers)
        self.sentence_segmenter = ragas.get_segmenter(
            language=self.faithfulness.nli_statements_message.language, clean=False
        )

    @classmethod
    def evaluate(self, span) -> t.Optional[dict]:
        if not self.enabled:
            return None

        llmobs_metadata = {}
        score = np.nan
        LLMObs = self.llmobs
        with LLMObs.workflow("ragas_faithfulness") as workflow:
            try:
                workflow.service = "ragas"

                faithfulness_inputs = self._extract_faithfulness_inputs(span)
                if faithfulness_inputs is None:
                    logger.debug(
                        "Failed to extract question and context from span sampled for ragas_faithfulness evaluation"
                    )
                    return None

                question, answer, context_str = (
                    faithfulness_inputs.question,
                    faithfulness_inputs.answer,
                    faithfulness_inputs.context,
                )

                statements_prompt = self._create_statements_prompt(question, answer)

                statements = self.faithfulness.llm.generate_text(statements_prompt)

                statements = self.statements_output_parser.parse(statements.generations[0][0].text)

                if statements is None:
                    return None
                statements = [item["simpler_statements"] for item in statements.dicts()]
                statements = [item for sublist in statements for item in sublist]

                llmobs_metadata["statements"] = statements

                assert isinstance(statements, t.List), "statements must be a list"

                p_value = self._create_nli_prompt(statements, context_str)

                nli_result = self.faithfulness.llm.generate_text(p_value)

                nli_result_text = [nli_result.generations[0][i].text for i in range(self.faithfulness._reproducibility)]
                faithfulness_list = [self.faithfulness_output_parser.parse(text) for text in nli_result_text]

                faithfulness_list = [faith.dicts() for faith in faithfulness_list if faith is not None]

                llmobs_metadata["faithfulness_list"] = faithfulness_list

                if faithfulness_list:
                    faithfulness_list = self.ensembler.from_discrete(
                        faithfulness_list,
                        "verdict",
                    )
                    faithfulness_list = StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
                else:
                    return None

                score = self.compute_score(faithfulness_list)

                if np.isnan(score):
                    return None

                exported_ragas_span = LLMObs.export_span()
                return {
                    "span_id": span.get("span_id"),
                    "trace_id": span.get("trace_id"),
                    "score_value": score,
                    "ml_app": config._llmobs_ml_app,
                    "timestamp_ms": math.floor(time.time() * 1000),
                    "metric_type": RagasFaithfulnessEvaluator.METRIC_TYPE,
                    "label": RagasFaithfulnessEvaluator.LABEL,
                    "metadata": llmobs_metadata,
                }
            finally:
                LLMObs.annotate(
                    input_data={
                        "answer": answer,
                        "question": question,
                        "context_str": context_str,
                    },
                    output_data=score,
                    metadata=llmobs_metadata,
                )

    def _extract_faithfulness_inputs(self, span: dict) -> t.Optional[FaithfulnessInputs]:
        with self.llmobs.workflow("ragas.extract_faithfulness_inputs"):
            self.llmobs.annotate(input_data=span)
            question, answer, context = None, None, None

            meta_io = span.get("meta")
            if meta_io is None:
                return None

            meta_input = meta_io.get("input")
            meta_output = meta_io.get("output")
            if meta_input or meta_output is None:
                return None

            prompt = meta_input.get("prompt")
            if prompt is None:
                return
            prompt_variables = prompt.get("variables")

            messages = meta_output.get("messages")
            if messages is not None and len(messages) > 0:
                answer = messages[-1].get("content")

            if prompt_variables:
                question = prompt_variables.get("question")
                context = prompt_variables.get("context")

            try:
                self.llmobs.annotate(output_data={"question": question, "context": context, "answer": answer})
                return FaithfulnessInputs(question=question, context=context, answer=answer)
            except ValidationError:
                return None

    def _create_statements_prompt(self, answer, question):
        with self.llmobs.task("ragas.create_statements_prompt") as task:
            task.service = "ragas"
            sentences = self.sentence_segmenter.segment(answer)
            sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
            sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
            return self.statement_prompt.format(question=question, answer=answer, sentences=sentences)

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
                score = np.nan
            self.llmobs.annotate(
                metadata={
                    "faithful_statements": faithful_statements,
                    "num_statements": num_statements,
                },
                output_data=score,
            )
            return score
