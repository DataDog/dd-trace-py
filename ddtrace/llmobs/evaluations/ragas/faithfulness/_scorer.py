import json
import typing

from langchain_core.pydantic_v1 import ValidationError
import numpy as np
from ragas.llms import llm_factory
from ragas.llms.output_parser import RagasoutputParser
from ragas.llms.output_parser import get_json_format_instructions
from ragas.metrics import faithfulness
from ragas.metrics.base import ensembler
from ragas.metrics.base import get_segmenter

from ddtrace.internal.logger import get_logger

from ....utils import LLMObsSpanContext
from ._utils import FaithfulnessInputs
from ._utils import StatementFaithfulnessAnswers
from ._utils import StatementsAnswers
from ._utils import context_parser
from ._utils import extract_inputs_from_messages_prompt


logger = get_logger(__name__)

# populate default values for faithfulness class
faithfulness.llm = llm_factory()

statement_prompt = faithfulness.statement_prompt

statements_output_instructions = get_json_format_instructions(StatementsAnswers)
statements_output_parser = RagasoutputParser(pydantic_object=StatementsAnswers)

faithfulness_output_instructions = get_json_format_instructions(StatementFaithfulnessAnswers)
faithfulness_output_parser = RagasoutputParser(pydantic_object=StatementFaithfulnessAnswers)

sentence_segmenter = get_segmenter(language=faithfulness.nli_statements_message.language, clean=False)


def create_statements_prompt(answer, question, llmobs_instance):
    with llmobs_instance.task("ragas.create_statements_prompt") as task:
        task.service = "ragas"
        sentences = sentence_segmenter.segment(answer)
        sentences = [sentence for sentence in sentences if sentence.strip().endswith(".")]
        sentences = "\n".join([f"{i}:{x}" for i, x in enumerate(sentences)])
        return statement_prompt.format(question=question, answer=answer, sentences=sentences)


def create_nli_prompt(statements, context_str, llmobs_instance):
    with llmobs_instance.task("ragas.create_nli_prompt") as task:
        task.service = "ragas"
        statements_str: str = json.dumps(statements)
        prompt_value = faithfulness.nli_statements_message.format(context=context_str, statements=statements_str)
        return prompt_value


def compute_score(answers, llmobs_instance):
    with llmobs_instance.task("ragas.compute_score") as task:
        task.service = "ragas"
        faithful_statements = sum(1 if answer.verdict else 0 for answer in answers.__root__)
        num_statements = len(answers.__root__)
        if num_statements:
            score = faithful_statements / num_statements
        else:
            score = np.nan
        return score


def extract_question_and_context_using_llm(messages, llmobs_instance):
    with llmobs_instance.workflow("ragas.infer_context"):
        extracted_inputs = faithfulness.llm.generate_text(
            prompt=extract_inputs_from_messages_prompt.format(messages=messages)
        )
        statements = context_parser.parse(extracted_inputs.generations[0][0].text)
        llmobs_instance.annotate(
            input_data=messages, output_data={"question": statements.question, "context": statements.context}
        )
        return statements.question, statements.context


def extract_faithfulness_inputs(span: LLMObsSpanContext, llmobs_instance) -> typing.Optional[FaithfulnessInputs]:
    question, answer, context_str = None, None, None

    if span.meta.output.messages is not None and len(span.meta.output.messages) > 0:
        answer = span.meta.output.messages[-1].get("content")
    if span.meta.input.prompt is not None:
        variables = span.meta.input.prompt.variables.model_dump()
        question = variables.get("question")
        if not question and span.meta.input.messages is not None and len(span.meta.input.messages) > 0:
            question = span.meta.input.messages[-1].get("content")
        context_str = variables.get("context")

    if question is None or context_str is None:
        # fallback to using llm
        question, context_str = extract_question_and_context_using_llm(span, llmobs_instance)

    try:
        return FaithfulnessInputs(question=question, context=context_str, answer=answer)
    except ValidationError as e:
        logger.debug("Failed to validate faithfulness inputs", e)
        return None


def score_faithfulness(span, llmobs_instance, shutdown_event):
    with llmobs_instance.workflow("ragas.faithfulness") as workflow:
        token_usage = {"input_tokens": 0, "output_tokens": 0}

        workflow.service = "ragas"

        faithfulness_inputs = extract_faithfulness_inputs(span, llmobs_instance)
        if faithfulness_inputs is None:
            return np.nan, llmobs_instance.export_span()

        if shutdown_event.is_set():
            return np.nan

        question, answer, context_str = (
            faithfulness_inputs.question,
            faithfulness_inputs.answer,
            faithfulness_inputs.context,
        )

        statements_prompt = create_statements_prompt(question, answer, llmobs_instance=llmobs_instance)

        statements = faithfulness.llm.generate_text(statements_prompt)
        if shutdown_event.is_set():
            return np.nan, llmobs_instance.export_span()

        usage = statements.llm_output.get("token_usage")
        if usage:
            token_usage["input_tokens"] += usage.get("prompt_tokens") if usage.get("prompt_tokens") else 0
            token_usage["output_tokens"] += usage.get("completion_tokens") if usage.get("completion_tokens") else 0

        statements = statements_output_parser.parse(statements.generations[0][0].text)

        if statements is None:
            return np.nan
        statements = [item["simpler_statements"] for item in statements.dicts()]
        statements = [item for sublist in statements for item in sublist]

        assert isinstance(statements, typing.List), "statements must be a list"

        p_value = create_nli_prompt(statements, context_str, llmobs_instance=llmobs_instance)

        nli_result = faithfulness.llm.generate_text(p_value)

        usage = nli_result.llm_output.get("token_usage")
        if usage:
            token_usage["input_tokens"] += usage.get("prompt_tokens") if usage.get("completion_tokens") else 0
            token_usage["output_tokens"] += usage.get("prompt_tokens") if usage.get("completion_tokens") else 0

        nli_result_text = [nli_result.generations[0][i].text for i in range(faithfulness._reproducibility)]
        faithfulness_list = [faithfulness_output_parser.parse(text) for text in nli_result_text]

        faithfulness_list = [faith.dicts() for faith in faithfulness_list if faith is not None]

        if faithfulness_list:
            faithfulness_list = ensembler.from_discrete(
                faithfulness_list,
                "verdict",
            )

            faithfulness_list = StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
        else:
            return np.nan, llmobs_instance.export_span()

        score = compute_score(faithfulness_list, llmobs_instance=llmobs_instance)
        llmobs_instance.annotate(
            input_data={
                "answer": answer,
                "question": question,
                "context_str": context_str,
            },
            output_data=score,
            metadata=token_usage,
        )
        return score, llmobs_instance.export_span()
