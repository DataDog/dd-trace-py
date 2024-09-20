import json
import math
import time
import typing
from typing import Optional

from langchain_core.pydantic_v1 import ValidationError
import numpy as np
from ragas.llms import llm_factory
from ragas.llms.output_parser import RagasoutputParser
from ragas.llms.output_parser import get_json_format_instructions
from ragas.metrics import faithfulness
from ragas.metrics.base import ensembler
from ragas.metrics.base import get_segmenter

from ddtrace import config
from ddtrace.internal.logger import get_logger

from .utils import FaithfulnessInputs
from .utils import StatementFaithfulnessAnswers
from .utils import StatementsAnswers
from .utils import context_parser
from .utils import extract_inputs_from_messages_prompt


logger = get_logger(__name__)

# populate default values for faithfulness class
faithfulness.llm = llm_factory()

statement_prompt = faithfulness.statement_prompt

statements_output_instructions = get_json_format_instructions(StatementsAnswers)
statements_output_parser = RagasoutputParser(pydantic_object=StatementsAnswers)

faithfulness_output_instructions = get_json_format_instructions(StatementFaithfulnessAnswers)
faithfulness_output_parser = RagasoutputParser(pydantic_object=StatementFaithfulnessAnswers)

sentence_segmenter = get_segmenter(language=faithfulness.nli_statements_message.language, clean=False)


def create_statements_prompt(answer, question, llmobs_service):
    with llmobs_service.task("ragas.create_statements_prompt") as task:
        task.service = "ragas"
        sentences = sentence_segmenter.segment(answer)
        sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
        sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
        return statement_prompt.format(question=question, answer=answer, sentences=sentences)


def create_nli_prompt(statements, context_str, llmobs_service):
    with llmobs_service.task("ragas.create_nli_prompt") as task:
        task.service = "ragas"
        statements_str: str = json.dumps(statements)
        prompt_value = faithfulness.nli_statements_message.format(context=context_str, statements=statements_str)
        return prompt_value


def compute_score(answers, llmobs_service):
    with llmobs_service.task("ragas.compute_score") as task:
        task.service = "ragas"
        faithful_statements = sum(1 if answer.verdict else 0 for answer in answers.__root__)
        num_statements = len(answers.__root__)
        if num_statements:
            score = faithful_statements / num_statements
        else:
            score = np.nan
        llmobs_service.annotate(
            metadata={
                "faithful_statements": faithful_statements,
                "num_statements": num_statements,
            },
            output_data=score,
        )
        return score


def extract_question_and_context_using_llm(messages, llmobs_service):
    with llmobs_service.workflow("ragas.extract_question_and_context_using_llm"):
        llmobs_service.annotate(input_data=messages)
        extracted_inputs = faithfulness.llm.generate_text(
            prompt=extract_inputs_from_messages_prompt.format(messages=messages)
        )
        statements = context_parser.parse(extracted_inputs.generations[0][0].text)
        llmobs_service.annotate(
            input_data=messages, output_data={"question": statements.question, "context": statements.context}
        )
        llmobs_service.annotate(output_data={"question": statements.question, "context": statements.context})
        return statements.question, statements.context


def extract_faithfulness_inputs(span: dict, llmobs_service) -> typing.Optional[FaithfulnessInputs]:
    with llmobs_service.workflow("ragas.extract_faithfulness_inputs"):
        llmobs_service.annotate(input_data=span)
        question, answer, context_str = None, None, None

        meta_io = span.get("meta")
        if meta_io is None:
            return None

        meta_input = meta_io.get("input")
        meta_output = meta_io.get("output")

        if meta_input or meta_output is None:
            return None

        messages = meta_output.get("messages")
        if messages is not None and len(messages) > 0:
            answer = messages[-1].get("content")

        prompt = meta_input.get("prompt")
        question = None
        context = None
        if prompt is not None and prompt.get("variables") is not None:
            variables = prompt.get("variables")
            question = variables.get("question")
            context = variables.get("context")

        if question is None or context is None:
            question, context_str = extract_question_and_context_using_llm(span, llmobs_service)
        try:
            llmobs_service.annotate(output_data={"question": question, "context": context, "answer": answer})
            return FaithfulnessInputs(question=question, context=context_str, answer=answer)
        except ValidationError as e:
            logger.debug("Failed to validate faithfulness inputs", e)
            return None


def score_faithfulness(span, llmobs_service):
    llmobs_metadata = {}
    token_usage = {"input_tokens": 0, "output_tokens": 0}
    score = np.nan
    with llmobs_service.workflow("ragas.faithfulness") as workflow:
        try:
            workflow.service = "ragas"

            faithfulness_inputs = extract_faithfulness_inputs(span, llmobs_service)
            if faithfulness_inputs is None:
                return np.nan, None, llmobs_service.export_span()

            question, answer, context_str = (
                faithfulness_inputs.question,
                faithfulness_inputs.answer,
                faithfulness_inputs.context,
            )

            statements_prompt = create_statements_prompt(question, answer, llmobs_service=llmobs_service)

            statements = faithfulness.llm.generate_text(statements_prompt)

            usage = statements.llm_output.get("token_usage")
            if usage:
                token_usage["input_tokens"] += usage.get("prompt_tokens") if usage.get("prompt_tokens") else 0
                token_usage["output_tokens"] += usage.get("completion_tokens") if usage.get("completion_tokens") else 0

            statements = statements_output_parser.parse(statements.generations[0][0].text)

            if statements is None:
                return np.nan
            statements = [item["simpler_statements"] for item in statements.dicts()]
            statements = [item for sublist in statements for item in sublist]

            llmobs_metadata["statements"] = statements

            assert isinstance(statements, typing.List), "statements must be a list"

            p_value = create_nli_prompt(statements, context_str, llmobs_service=llmobs_service)

            nli_result = faithfulness.llm.generate_text(p_value)

            usage = nli_result.llm_output.get("token_usage")
            if usage:
                token_usage["input_tokens"] += usage.get("prompt_tokens") if usage.get("completion_tokens") else 0
                token_usage["output_tokens"] += usage.get("prompt_tokens") if usage.get("completion_tokens") else 0

            nli_result_text = [nli_result.generations[0][i].text for i in range(faithfulness._reproducibility)]
            faithfulness_list = [faithfulness_output_parser.parse(text) for text in nli_result_text]

            faithfulness_list = [faith.dicts() for faith in faithfulness_list if faith is not None]

            llmobs_metadata["faithfulness_list"] = faithfulness_list

            if faithfulness_list:
                faithfulness_list = ensembler.from_discrete(
                    faithfulness_list,
                    "verdict",
                )

                faithfulness_list = StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
            else:
                return np.nan, None, llmobs_service.export_span()
            score = compute_score(faithfulness_list, llmobs_service=llmobs_service)
            return score, faithfulness_list.json(), llmobs_service.export_span()
        finally:
            llmobs_metadata.update(token_usage)
            llmobs_service.annotate(
                input_data={
                    "answer": answer,
                    "question": question,
                    "context_str": context_str,
                },
                output_data=score,
                metadata=llmobs_metadata,
            )


class RagasFaithfulnessEvaluator:
    label = "ragas_faithfulness"
    metric_type = "score"
    llmobs_service = None

    def __init__(self, llmobs_service):
        RagasFaithfulnessEvaluator.llmobs_service = llmobs_service

    @classmethod
    def evaluate(cls, span) -> Optional[dict]:
        if cls.llmobs_service is None:
            return None

        score, faithfulness_list, exported_span = score_faithfulness(span, cls.llmobs_service)
        if math.isnan(score):
            return None
        return {
            "span_id": exported_span.get("span_id"),
            "trace_id": exported_span.get("trace_id"),
            "score_value": 1,
            "ml_app": config._llmobs_ml_app,
            "timestamp_ms": math.floor(time.time() * 1000),
            "metric_type": cls.metric_type,
            "label": cls.label,
            "metadata": {"ragas.faithfulness_list": faithfulness_list},
        }
