import json
import typing

import numpy as np
from ragas.llms import llm_factory
from ragas.llms.output_parser import RagasoutputParser
from ragas.llms.output_parser import get_json_format_instructions
from ragas.metrics import faithfulness
from ragas.metrics.base import ensembler
from ragas.metrics.base import get_segmenter

from ....utils import EvaluationMetric
from ....utils import LLMObsSpanContext
from ._utils import StatementFaithfulnessAnswers
from ._utils import StatementsAnswers
from ._utils import context_extractor_prompt
from ._utils import context_parser


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
            # logger.warning("No statements were generated from the answer.")
            score = np.nan
        return score


def infer_question_and_context(messages, llmobs_instance):
    with llmobs_instance.workflow("_ragas.infer_context"):
        inferred_context = faithfulness.llm.generate_text(prompt=context_extractor_prompt.format(messages=messages))
        statements = context_parser.parse(inferred_context.generations[0][0].text)
        llmobs_instance.annotate(
            input_data=messages, output_data={"question": statements.question, "context": statements.context}
        )
        return statements.question, statements.context


async def extract_inputs(span, llmobs_instance):
    answer = ""
    question = ""
    context = ""
    if span.meta.output.messages and len(span.meta.output.messages) > 0:
        answer = span.meta.output.messages[-1]["content"]
    question, context = await infer_question_and_context(span.meta.input.messages, llmobs_instance=llmobs_instance)
    return question, context, answer


def extract_inputs_sync(span, llmobs_instance):
    answer = ""
    question = ""
    context = ""
    if span.meta.output.messages and len(span.meta.output.messages) > 0:
        answer = span.meta.output.messages[-1]["content"]
    question, context = infer_question_and_context(span.meta.input.messages, llmobs_instance=llmobs_instance)
    return question, context, answer


def score_faithfulness_sync(span, llmobs_instance):
    with llmobs_instance.workflow("_ragas.faithfulness") as workflow:
        workflow.service = "_ragas"

        question, answer, context_str = extract_inputs_sync(span, llmobs_instance)
        statements_prompt = create_statements_prompt(question, answer, llmobs_instance=llmobs_instance)

        statements = faithfulness.llm.generate_text(statements_prompt)

        statements = statements_output_parser.parse(statements.generations[0][0].text)

        if statements is None:
            return np.nan
        statements = [item["simpler_statements"] for item in statements.dicts()]
        statements = [item for sublist in statements for item in sublist]

        assert isinstance(statements, typing.List), "statements must be a list"

        p_value = create_nli_prompt(statements, context_str, llmobs_instance=llmobs_instance)

        nli_result = faithfulness.llm.generate_text(p_value)

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
            return np.nan

        score = compute_score(faithfulness_list, llmobs_instance=llmobs_instance)
        llmobs_instance.annotate(
            input_data={
                "answer": answer,
                "question": question,
                "context_str": context_str,
            },
            output_data=score,
        )
        return score, llmobs_instance.export_span()


async def score_faithfulness(span, llmobs_instance, shutdown_event):
    with llmobs_instance.workflow("_ragas.faithfulness") as workflow:
        workflow.service = "_ragas"

        print(shutdown_event.is_set())
        if shutdown_event.is_set():
            return None, llmobs_instance.export_span()

        question, answer, context_str = await extract_inputs(span, llmobs_instance)
        statements_prompt = create_statements_prompt(question, answer, llmobs_instance=llmobs_instance)

        print(shutdown_event.is_set())
        if shutdown_event.is_set():
            return None, llmobs_instance.export_span()

        print(shutdown_event.is_set())
        statements = await faithfulness.llm.generate(statements_prompt)

        statements = await statements_output_parser.aparse(
            statements.generations[0][0].text,
            statements_prompt,
            faithfulness.llm,
            faithfulness.max_retries,
        )
        if statements is None:
            return np.nan
        statements = [item["simpler_statements"] for item in statements.dicts()]
        statements = [item for sublist in statements for item in sublist]

        assert isinstance(statements, typing.List), "statements must be a list"

        p_value = create_nli_prompt(statements, context_str, llmobs_instance=llmobs_instance)

        if shutdown_event.is_set():
            return None, llmobs_instance.export_span()

        nli_result = await faithfulness.llm.generate(p_value)

        nli_result_text = [nli_result.generations[0][i].text for i in range(faithfulness._reproducibility)]
        faithfulness_list = [
            await faithfulness_output_parser.aparse(text, p_value, faithfulness.llm, faithfulness.max_retries)
            for text in nli_result_text
        ]

        faithfulness_list = [faith.dicts() for faith in faithfulness_list if faith is not None]

        if faithfulness_list:
            faithfulness_list = ensembler.from_discrete(
                faithfulness_list,
                "verdict",
            )

            faithfulness_list = StatementFaithfulnessAnswers.parse_obj(faithfulness_list)
        else:
            return np.nan

        score = compute_score(faithfulness_list, llmobs_instance=llmobs_instance)
        llmobs_instance.annotate(
            input_data={
                "answer": answer,
                "question": question,
                "context_str": context_str,
            },
            output_data=score,
        )
        return score, llmobs_instance.export_span()
