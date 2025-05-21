import os

import mock


try:
    import vcr
except ImportError:
    vcr = None

import ddtrace
from ddtrace.ext import SpanTypes
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import INTEGRATION, LLMOBS_TRACE_ID
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._utils import _get_span_name
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.trace import Span


if vcr:
    logs_vcr = vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "llmobs_cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization", "api-key", "x-api-key", ("DD-API-KEY", "XXXXXX")],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
else:
    logs_vcr = None


def _expected_llmobs_tags(span, error=None, tags=None, session_id=None):
    if tags is None:
        tags = {}
    expected_tags = [
        "version:{}".format(tags.get("version", "")),
        "env:{}".format(tags.get("env", "")),
        "service:{}".format(tags.get("service", "tests.llmobs")),
        "source:integration",
        "ml_app:{}".format(tags.get("ml_app", "unnamed-ml-app")),
        "ddtrace.version:{}".format(ddtrace.__version__),
        "language:python",
    ]
    if error:
        expected_tags.append("error:1")
        expected_tags.append("error_type:{}".format(error))
    else:
        expected_tags.append("error:0")
    if session_id:
        expected_tags.append("session_id:{}".format(session_id))
    if span._get_ctx_item(INTEGRATION):
        expected_tags.append("integration:{}".format(span._get_ctx_item(INTEGRATION)))
    if tags:
        expected_tags.extend(
            "{}:{}".format(k, v) for k, v in tags.items() if k not in ("version", "env", "service", "ml_app")
        )
    return expected_tags


def _expected_llmobs_llm_span_event(
    span,
    span_kind="llm",
    prompt=None,
    input_messages=None,
    input_documents=None,
    output_messages=None,
    output_value=None,
    metadata=None,
    token_metrics=None,
    model_name=None,
    model_provider=None,
    tags=None,
    session_id=None,
    error=None,
    error_message=None,
    error_stack=None,
    span_links=False,
):
    """
    Helper function to create an expected LLM span event.
    span_kind: either "llm" or "agent" or "embedding"
    input_messages: list of input messages in format {"content": "...", "optional_role", "..."}
    output_messages: list of output messages in format {"content": "...", "optional_role", "..."}
    metadata: dict of metadata key value pairs
    token_metrics: dict of token metrics (e.g. prompt_tokens, completion_tokens, total_tokens)
    model_name: name of the model
    model_provider: name of the model provider
    tags: dict of tags to add/override on span
    session_id: session ID
    error: error type
    error_message: error message
    error_stack: error stack
    span_links: whether there are span links present on this span.
    """
    span_event = _llmobs_base_span_event(
        span, span_kind, tags, session_id, error, error_message, error_stack, span_links
    )
    meta_dict = {"input": {}, "output": {}}
    if span_kind == "llm":
        if input_messages is not None:
            input_messages = (
                [
                    input_message if input_message.get("role") is not None else {**input_message, "role": ""}
                    for input_message in input_messages
                ]
                if isinstance(input_messages, list)
                else input_messages
            )
            meta_dict["input"].update({"messages": input_messages})
        if output_messages is not None:
            output_messages = (
                [
                    output_message if output_message.get("role") is not None else {**output_message, "role": ""}
                    for output_message in output_messages
                ]
                if isinstance(output_messages, list)
                else output_messages
            )
            meta_dict["output"].update({"messages": output_messages})
        if prompt is not None:
            meta_dict["input"].update({"prompt": prompt})
    if span_kind == "embedding":
        if input_documents is not None:
            meta_dict["input"].update({"documents": input_documents})
        if output_value is not None:
            meta_dict["output"].update({"value": output_value})
    if not meta_dict["input"]:
        meta_dict.pop("input")
    if not meta_dict["output"]:
        meta_dict.pop("output")
    if model_name is not None:
        meta_dict.update({"model_name": model_name})
    if model_provider is not None:
        meta_dict.update({"model_provider": model_provider})
    meta_dict.update({"metadata": metadata or {}})
    span_event["meta"].update(meta_dict)
    if token_metrics is not None:
        span_event["metrics"].update(token_metrics)
    return span_event


def _expected_llmobs_non_llm_span_event(
    span,
    span_kind,
    input_value=None,
    output_value=None,
    output_documents=None,
    metadata=None,
    token_metrics=None,
    tags=None,
    session_id=None,
    error=None,
    error_message=None,
    error_stack=None,
    span_links=False,
):
    """
    Helper function to create an expected span event of type (workflow, task, tool, retrieval).
    span_kind: one of "workflow", "task", "tool", "retrieval"
    input_value: input value string
    output_value: output value string
    metadata: dict of metadata key value pairs
    token_metrics: dict of token metrics (e.g. prompt_tokens, completion_tokens, total_tokens)
    tags: dict of tags to add/override on span
    session_id: session ID
    error: error type
    error_message: error message
    error_stack: error stack
    span_links: whether there are span links present on this span.
    """
    span_event = _llmobs_base_span_event(
        span, span_kind, tags, session_id, error, error_message, error_stack, span_links
    )
    meta_dict = {"input": {}, "output": {}}
    if span_kind == "retrieval":
        if input_value is not None:
            meta_dict["input"].update({"value": input_value})
        if output_documents is not None:
            meta_dict["output"].update({"documents": output_documents})
        if output_value is not None:
            meta_dict["output"].update({"value": output_value})
    if input_value is not None:
        meta_dict["input"].update({"value": input_value})
    meta_dict.update({"metadata": metadata or {}})
    if output_value is not None:
        meta_dict["output"].update({"value": output_value})
    if not meta_dict["input"]:
        meta_dict.pop("input")
    if not meta_dict["output"]:
        meta_dict.pop("output")
    span_event["meta"].update(meta_dict)
    if token_metrics is not None:
        span_event["metrics"].update(token_metrics)
    return span_event


def _llmobs_base_span_event(
    span,
    span_kind,
    tags=None,
    session_id=None,
    error=None,
    error_message=None,
    error_stack=None,
    span_links=False,
):
    span_event = {
        "trace_id": format_trace_id(span._get_ctx_item(LLMOBS_TRACE_ID)),
        "span_id": str(span.span_id),
        "parent_id": _get_llmobs_parent_id(span),
        "name": _get_span_name(span),
        "start_ns": span.start_ns,
        "duration": span.duration_ns,
        "status": "error" if error else "ok",
        "meta": {"span.kind": span_kind},
        "metrics": {},
        "tags": _expected_llmobs_tags(span, tags=tags, error=error, session_id=session_id),
        "_dd": {"span_id": str(span.span_id), "trace_id": format_trace_id(span.trace_id), "apm_trace_id": format_trace_id(span.trace_id)},
    }
    if session_id:
        span_event["session_id"] = session_id
    if error:
        span_event["meta"]["error.type"] = error
        span_event["meta"]["error.message"] = error_message
        span_event["meta"]["error.stack"] = error_stack
    if span_links:
        span_event["span_links"] = mock.ANY
    return span_event


def _get_llmobs_parent_id(span: Span):
    if span._get_ctx_item(PARENT_ID_KEY):
        return span._get_ctx_item(PARENT_ID_KEY)
    if not span._parent:
        return "undefined"
    parent = span._parent
    while parent is not None:
        if parent.span_type == SpanTypes.LLM:
            return str(parent.span_id)
        parent = parent._parent


def _expected_llmobs_eval_metric_event(
    metric_type,
    label,
    ml_app,
    tag_key=None,
    tag_value=None,
    span_id=None,
    trace_id=None,
    timestamp_ms=None,
    categorical_value=None,
    score_value=None,
    numerical_value=None,
    tags=None,
    metadata=None,
):
    eval_metric_event = {
        "join_on": {},
        "metric_type": metric_type,
        "label": label,
        "tags": [
            "ddtrace.version:{}".format(ddtrace.__version__),
            "ml_app:{}".format(ml_app if ml_app is not None else "unnamed-ml-app"),
        ],
    }
    if tag_key is not None and tag_value is not None:
        eval_metric_event["join_on"]["tag"] = {"key": tag_key, "value": tag_value}
    if span_id is not None and trace_id is not None:
        eval_metric_event["join_on"]["span"] = {"span_id": span_id, "trace_id": trace_id}
    if categorical_value is not None:
        eval_metric_event["categorical_value"] = categorical_value
    if score_value is not None:
        eval_metric_event["score_value"] = score_value
    if numerical_value is not None:
        eval_metric_event["numerical_value"] = numerical_value
    if tags is not None:
        eval_metric_event["tags"] = tags
    if timestamp_ms is not None:
        eval_metric_event["timestamp_ms"] = timestamp_ms
    else:
        eval_metric_event["timestamp_ms"] = mock.ANY

    if ml_app is not None:
        eval_metric_event["ml_app"] = ml_app
    if metadata is not None:
        eval_metric_event["metadata"] = metadata
    return eval_metric_event


def _completion_event():
    return {
        "kind": "llm",
        "span_id": "12345678901",
        "trace_id": "98765432101",
        "parent_id": "",
        "session_id": "98765432101",
        "name": "completion_span",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223236,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "llm",
            "model_name": "ada",
            "model_provider": "openai",
            "input": {
                "messages": [{"content": "who broke enigma?"}],
            },
            "output": {
                "messages": [
                    {
                        "content": "\n\nThe Enigma code was broken by a team of codebreakers at Bletchley Park, led by mathematician Alan Turing."  # noqa: E501
                    }
                ]
            },
            "metadata": {"temperature": 0, "max_tokens": 256},
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def _chat_completion_event():
    return {
        "span_id": "12345678902",
        "trace_id": "98765432102",
        "parent_id": "",
        "session_id": "98765432102",
        "name": "chat_completion_span",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "llm",
            "model_name": "gpt-3.5-turbo",
            "model_provider": "openai",
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an evil dark lord looking for his one ring to rule them all",
                    },
                    {"role": "user", "content": "I am a hobbit looking to go to Mordor"},
                ],
            },
            "output": {
                "messages": [
                    {
                        "content": "Ah, a bold and foolish hobbit seeking to challenge my dominion in Mordor. Very well, little creature, I shall play along. But know that I am always watching, and your quest will not go unnoticed",  # noqa: E501
                        "role": "assistant",
                    },
                ]
            },
            "metadata": {"temperature": 0.9, "max_tokens": 256},
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def _chat_completion_event_with_unserializable_field():
    return {
        "span_id": "12345678902",
        "trace_id": "98765432102",
        "parent_id": "",
        "session_id": "98765432102",
        "name": "chat_completion_span",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "llm",
            "model_name": "gpt-3.5-turbo",
            "model_provider": "openai",
            "metadata": {"unserializable": object()},
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an evil dark lord looking for his one ring to rule them all",
                    },
                    {"role": "user", "content": "I am a hobbit looking to go to Mordor"},
                ],
                "parameters": {"temperature": 0.9, "max_tokens": 256},
            },
            "output": {
                "messages": [
                    {
                        "content": "Ah, a bold and foolish hobbit seeking to challenge my dominion in Mordor. Very well, little creature, I shall play along. But know that I am always watching, and your quest will not go unnoticed",  # noqa: E501
                        "role": "assistant",
                    },
                ]
            },
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def _large_event():
    return {
        "span_id": "12345678903",
        "trace_id": "98765432103",
        "parent_id": "",
        "session_id": "98765432103",
        "name": "large_span",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "llm",
            "model_name": "gpt-3.5-turbo",
            "model_provider": "openai",
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an evil dark lord looking for his one ring to rule them all",
                    },
                    {"role": "user", "content": "I am a hobbit looking to go to Mordor"},
                ],
                "parameters": {"temperature": 0.9, "max_tokens": 256},
            },
            "output": {
                "messages": [
                    {
                        "content": "A" * 900_000,
                        "role": "assistant",
                    },
                ]
            },
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def _oversized_llm_event():
    return {
        "span_id": "12345678904",
        "trace_id": "98765432104",
        "parent_id": "",
        "session_id": "98765432104",
        "name": "oversized_llm_event",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "llm",
            "model_name": "gpt-3.5-turbo",
            "model_provider": "openai",
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an evil dark lord looking for his one ring to rule them all",
                    },
                    {"role": "user", "content": "A" * 700_000},
                ],
                "parameters": {"temperature": 0.9, "max_tokens": 256},
            },
            "output": {
                "messages": [
                    {
                        "content": "A" * 700_000,
                        "role": "assistant",
                    },
                ]
            },
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def _oversized_workflow_event():
    return {
        "span_id": "12345678905",
        "trace_id": "98765432105",
        "parent_id": "",
        "session_id": "98765432105",
        "name": "oversized_workflow_event",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "workflow",
            "input": {"value": "A" * 700_000},
            "output": {"value": "A" * 700_000},
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def _oversized_retrieval_event():
    return {
        "span_id": "12345678906",
        "trace_id": "98765432106",
        "parent_id": "",
        "session_id": "98765432106",
        "name": "oversized_retrieval_event",
        "tags": ["version:", "env:", "service:tests.llmobs", "source:integration"],
        "start_ns": 1707763310981223936,
        "duration": 12345678900,
        "status": "ok",
        "meta": {
            "span.kind": "retrieval",
            "input": {"documents": {"content": "A" * 700_000}},
            "output": {"value": "A" * 700_000},
        },
        "metrics": {"input_tokens": 64, "output_tokens": 128, "total_tokens": 192},
    }


def expected_ragas_trace_tags():
    return [
        "version:",
        "env:",
        "service:tests.llmobs",
        "source:integration",
        "ml_app:unnamed-ml-app",
        "ddtrace.version:{}".format(ddtrace.__version__),
        "language:python",
        "error:0",
        "runner.integration:ragas",
    ]


default_ragas_inputs = {
    "question": "What is the capital of France?",
    "context": "The capital of France is Paris.",
    "answer": "The capital of France is Paris",
}


def _llm_span_with_expected_ragas_inputs_in_prompt(ragas_inputs=None):
    if not ragas_inputs:
        ragas_inputs = default_ragas_inputs

    return _expected_llmobs_llm_span_event(
        span=Span("dummy"),
        prompt={
            "variables": {"question": ragas_inputs["question"], "context": ragas_inputs["context"]},
        },
        output_messages=[{"content": ragas_inputs["answer"]}],
    )


def _llm_span_with_expected_ragas_inputs_in_messages(ragas_inputs=None):
    if not ragas_inputs:
        ragas_inputs = default_ragas_inputs

    return _expected_llmobs_llm_span_event(
        span=Span("dummy"),
        prompt={
            "variables": {"context": ragas_inputs["context"]},
        },
        input_messages=[{"content": ragas_inputs["question"]}],
        output_messages=[{"content": ragas_inputs["answer"]}],
    )


class DummyEvaluator:
    def __init__(self, llmobs_service, label="dummy"):
        self.llmobs_service = llmobs_service
        self.LABEL = label

    def run_and_submit_evaluation(self, span):
        self.llmobs_service.submit_evaluation(
            span_context=span,
            label=self.LABEL,
            value=1.0,
            metric_type="score",
        )


def _dummy_evaluator_eval_metric_event(span_id, trace_id, label=None):
    return LLMObsEvaluationMetricEvent(
        join_on={"span": {"span_id": span_id, "trace_id": trace_id}},
        score_value=1.0,
        ml_app="unnamed-ml-app",
        timestamp_ms=mock.ANY,
        metric_type="score",
        label=label or "dummy",
        tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:unnamed-ml-app"],
    )


def _expected_ragas_context_precision_spans(ragas_inputs=None):
    if not ragas_inputs:
        ragas_inputs = default_ragas_inputs
    return [
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": "undefined",
            "name": "dd-ragas.context_precision",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": "1.0"},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
            "span_links": mock.ANY,
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.extract_evaluation_inputs_from_span",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
    ]


def _expected_ragas_faithfulness_spans(ragas_inputs=None):
    if not ragas_inputs:
        ragas_inputs = default_ragas_inputs
    return [
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": "undefined",
            "name": "dd-ragas.faithfulness",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": "1.0"},
                "metadata": {
                    "statements": mock.ANY,
                    "faithfulness_list": mock.ANY,
                },
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.extract_evaluation_inputs_from_span",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.create_statements",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
            "span_links": mock.ANY,
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.create_statements_prompt",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {"span.kind": "task", "metadata": {}},
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.create_verdicts",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
            "span_links": mock.ANY,
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.create_natural_language_inference_prompt",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {"span.kind": "task", "metadata": {}},
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.compute_score",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "task",
                "output": {"value": "1.0"},
                "metadata": {"faithful_statements": 1, "num_statements": 1},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
    ]


def _expected_ragas_answer_relevancy_spans(ragas_inputs=None):
    if not ragas_inputs:
        ragas_inputs = default_ragas_inputs
    return [
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": "undefined",
            "name": "dd-ragas.answer_relevancy",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {"answer_classifications": mock.ANY, "strictness": mock.ANY},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
            "span_links": mock.ANY,
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.extract_evaluation_inputs_from_span",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
        {
            "trace_id": mock.ANY,
            "span_id": mock.ANY,
            "parent_id": mock.ANY,
            "name": "dd-ragas.calculate_similarity",
            "start_ns": mock.ANY,
            "duration": mock.ANY,
            "status": "ok",
            "meta": {
                "span.kind": "workflow",
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY},
        },
    ]


def _expected_span_link(span_event, link_from, link_to):
    return {
        "trace_id": span_event["trace_id"],
        "span_id": span_event["span_id"],
        "attributes": {"from": link_from, "to": link_to},
    }


class TestLLMObsSpanWriter(LLMObsSpanWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = []

    def enqueue(self, event):
        self.events.append(event)
        super().enqueue(event)


def _assert_span_link(from_span_event, to_span_event, from_io, to_io):
    """
    Assert that a span link exists between two span events, specifically the correct span ID and from/to specification.
    """
    found = False
    expected_to_span_id = "undefined" if not from_span_event else from_span_event["span_id"]
    for span_link in to_span_event["span_links"]:
        if span_link["span_id"] == expected_to_span_id:
            assert span_link["attributes"] == {"from": from_io, "to": to_io}
            found = True
            break
    assert found
