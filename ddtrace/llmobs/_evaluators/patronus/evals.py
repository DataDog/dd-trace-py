from typing import Optional

from patronus import Client

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import EVALUATION_KIND_METADATA
from ddtrace.llmobs._constants import FAITHFULNESS_DISAGREEMENTS_METADATA
from ddtrace.llmobs._constants import INTERNAL_CONTEXT_VARIABLE_KEYS
from ddtrace.llmobs._constants import INTERNAL_QUERY_VARIABLE_KEYS


logger = get_logger(__name__)

client = Client()


class BasePatronusEvaluator:
    INTEGRATION = "patronus"

    def __init__(self, llmobs_service):
        self.llmobs_service = llmobs_service

    def run_and_submit_evaluation(self, span_event: dict):
        raise NotImplementedError("not implemented")

    def extract_inputs(self, span_event: dict) -> Optional[dict]:
        """
        Extracts the question, answer, and context used as inputs to faithfulness
        evaluation from a span event.

        question - input.prompt.variables.question OR input.messages[-1].content
        context - input.prompt.variables.context
        answer - output.messages[-1].content
        """
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
            context = " ".join([prompt_variables.get(key) for key in context_keys if prompt_variables.get(key)])
            question = " ".join([prompt_variables.get(key) for key in question_keys if prompt_variables.get(key)])

        if not question and input_messages is not None and len(input_messages) > 0:
            question = input_messages[-1].get("content")

        if any(field is None for field in (question, context, answer)):
            logger.debug("Failed to extract inputs required for faithfulness evaluation")
            return None

        return {"question": question, "context": context, "answer": answer}


class PatronusHallucinationEvaluator(BasePatronusEvaluator):
    LABEL = "patronus_hallucination"
    METRIC_TYPE = "categorical"

    def run_and_submit_evaluation(self, span_event: dict):
        inputs = self.extract_inputs(span_event)
        if inputs is None:
            return
        result = None
        try:
            result = client.evaluate(
                evaluator="lynx",
                criteria="patronus:hallucination",
                evaluated_model_input=inputs["question"],
                evaluated_model_output=inputs["answer"],
                evaluated_model_retrieved_context=inputs["context"],
            )
        except Exception:
            logger.warning("patronus evaluation failed", exc_info=True)
        if result:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value="pass" if result.pass_ else "fail",
                metadata={
                    EVALUATION_KIND_METADATA: "faithfulness",
                    FAITHFULNESS_DISAGREEMENTS_METADATA: [
                        {"reasoning": result.explanation},
                    ],
                    "score_raw": result.score_raw,
                    "explanation": result.explanation,
                },
            )


class PatronusAnswerRelevanceEvaluator(BasePatronusEvaluator):
    LABEL = "patronus_answer_relevance"
    METRIC_TYPE = "categorical"

    def run_and_submit_evaluation(self, span_event: dict):
        inputs = self.extract_inputs(span_event)
        if inputs is None:
            return
        result = None
        try:
            result = client.evaluate(
                evaluator="answer-relevance",
                criteria="patronus:answer-relevance",
                evaluated_model_input=inputs["question"],
                evaluated_model_output=inputs["answer"],
                evaluated_model_retrieved_context=inputs["context"],
            )
        except Exception:
            logger.warning("patronus evaluation failed", exc_info=True)
        if result:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value="pass" if result.pass_ else "fail",
                metadata={
                    "score_raw": result.score_raw,
                    "explanation": result.explanation,
                },
            )


class PatronusContextRelevanceEvaluator(BasePatronusEvaluator):
    LABEL = "patronus_context_relevance"
    METRIC_TYPE = "categorical"

    def run_and_submit_evaluation(self, span_event: dict):
        inputs = self.extract_inputs(span_event)
        if inputs is None:
            return
        result = None
        try:
            result = client.evaluate(
                evaluator="context-relevance",
                criteria="patronus:context-relevance",
                evaluated_model_input=inputs["question"],
                evaluated_model_output=inputs["answer"],
                evaluated_model_retrieved_context=inputs["context"],
            )
        except Exception:
            logger.warning("patronus evaluation failed", exc_info=True)
        if result:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value="pass" if result.pass_ else "fail",
                metadata={
                    "score_raw": result.score_raw,
                    "explanation": result.explanation,
                },
            )


class PatronusPromptInjection(BasePatronusEvaluator):
    LABEL = "patronus_prompt_injection"
    METRIC_TYPE = "categorical"

    def run_and_submit_evaluation(self, span_event: dict):
        inputs = self.extract_inputs(span_event)
        if inputs is None:
            return
        result = None
        try:
            result = client.evaluate(
                evaluator="judge",
                criteria="patronus:prompt-injection",
                evaluated_model_input=inputs["question"],
                evaluated_model_output=inputs["answer"],
                evaluated_model_retrieved_context=inputs["context"],
            )
        except Exception:
            logger.warning("patronus evaluation failed", exc_info=True)
        if result:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value="pass" if result.pass_ else "fail",
                metadata={
                    "score_raw": result.score_raw,
                    "explanation": result.explanation,
                },
            )


class PatronusIsConcise(BasePatronusEvaluator):
    LABEL = "patronus_is_concise"
    METRIC_TYPE = "categorical"

    def run_and_submit_evaluation(self, span_event: dict):
        inputs = self.extract_inputs(span_event)
        if inputs is None:
            return
        result = None
        try:
            result = client.evaluate(
                evaluator="judge",
                criteria="patronus:is-concise",
                evaluated_model_output=inputs["answer"],
            )
        except Exception:
            logger.warning("patronus evaluation failed", exc_info=True)
        if result:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value="pass" if result.pass_ else "fail",
                metadata={
                    "score_raw": result.score_raw,
                    "explanation": result.explanation,
                },
            )


class PatronusToxicity(BasePatronusEvaluator):
    LABEL = "patronus_toxicity"
    METRIC_TYPE = "categorical"

    def run_and_submit_evaluation(self, span_event: dict):
        inputs = self.extract_inputs(span_event)
        if inputs is None:
            return
        result = None
        try:
            result = client.evaluate(
                evaluator="toxicity",
                criteria="patronus:toxicity",
                evaluated_model_output=inputs["answer"],
            )
        except Exception:
            logger.warning("patronus evaluation failed", exc_info=True)
        if result:
            self.llmobs_service.submit_evaluation(
                span_context={"trace_id": span_event.get("trace_id"), "span_id": span_event.get("span_id")},
                label=self.LABEL,
                metric_type=self.METRIC_TYPE,
                value="pass" if result.pass_ else "fail",
                metadata={
                    "score_raw": result.score_raw,
                    "explanation": result.explanation,
                },
            )
