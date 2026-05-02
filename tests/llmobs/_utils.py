import os

import mock  # type: ignore[import-untyped]

from ddtrace.llmobs.types import _ErrorField
from ddtrace.llmobs.types import _Meta
from ddtrace.llmobs.types import _SpanField


try:
    import vcr
except ImportError:
    vcr = None

import ddtrace
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import UNKNOWN_MODEL_NAME
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._llmobs import _STANDARD_INTEGRATION_SPAN_NAMES
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._writer import LLMObsEvaluationMetricEvent
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.trace import Span


DEEP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "l1": {
            "type": "object",
            "properties": {
                "l2": {
                    "type": "object",
                    "properties": {
                        "l3": {
                            "type": "object",
                            "properties": {
                                "l4": {
                                    "type": "object",
                                    "properties": {"l5": {"type": "string"}},
                                }
                            },
                        }
                    },
                }
            },
        }
    },
}


if vcr:
    logs_vcr = vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "..", "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=[
            "authorization",
            "OpenAI-Organization",
            "api-key",
            "x-api-key",
            ("DD-API-KEY", "XXXXXX"),
            ("DD-APPLICATION-KEY", "XXXXXX"),
        ],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
else:
    logs_vcr = None


def get_azure_openai_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "..", "cassettes", "azure_openai"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "api-key"],
        ignore_localhost=True,
    )


def get_vertexai_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "..", "cassettes", "vertexai"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-goog-api-key"],
        ignore_localhost=True,
    )


def get_bedrock_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "..", "cassettes", "bedrock"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "X-Amz-Security-Token"],
        ignore_localhost=True,
    )


def _expected_llmobs_tags(span, error=None, tags=None, session_id=None, is_decorator=False):
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
    if is_decorator:
        expected_tags.append("decorator:1")
    if tags:
        expected_tags.extend(
            "{}:{}".format(k, v) for k, v in tags.items() if k not in ("version", "env", "service", "ml_app")
        )
    return sorted(expected_tags)


def _expected_llmobs_llm_span_event(
    span,
    span_kind="llm",
    prompt=None,
    prompt_tracking_instrumentation_method=None,
    prompt_multimodal=None,
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
    tool_definitions=None,
    is_decorator=False,
    name=None,
    parent_id=None,
):
    """
    Helper function to create an expected LLM span event.
    span_kind: either "llm" or "agent" or "embedding"
    prompt: prompt metadata dict (id, version, variables, template)
    prompt_tracking_instrumentation_method: prompt tracking source tag ('auto' for auto-instrumented)
    prompt_multimodal: whether prompt contains multimodal inputs (True if present)
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
    tool_definitions: list of tool definitions that were available to the LLM
    is_decorator: whether the span was created via a decorator
    """
    span_event = _llmobs_base_span_event(
        span,
        span_kind,
        tags,
        session_id,
        error,
        error_message,
        error_stack,
        span_links,
        prompt_tracking_instrumentation_method,
        prompt_multimodal,
        is_decorator=is_decorator,
        name=name,
        parent_id=parent_id,
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
    if span_kind in ("llm", "embedding"):
        meta_dict["model_name"] = model_name if model_name is not None else UNKNOWN_MODEL_NAME
        meta_dict["model_provider"] = (model_provider or UNKNOWN_MODEL_PROVIDER).lower()
    elif model_name is not None:
        meta_dict["model_name"] = model_name
        meta_dict["model_provider"] = (model_provider or UNKNOWN_MODEL_PROVIDER).lower()
    if tool_definitions is not None:
        meta_dict["tool_definitions"] = tool_definitions
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
    prompt_tracking_instrumentation_method=None,
    prompt_multimodal=None,
    is_decorator=False,
    name=None,
    parent_id=None,
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
    prompt_tracking_instrumentation_method: prompt tracking source tag ('auto' for auto-instrumented)
    prompt_multimodal: whether prompt contains multimodal inputs (True if present)
    is_decorator: whether the span was created via a decorator
    """
    span_event = _llmobs_base_span_event(
        span,
        span_kind,
        tags,
        session_id,
        error,
        error_message,
        error_stack,
        span_links,
        prompt_tracking_instrumentation_method,
        prompt_multimodal,
        is_decorator=is_decorator,
        name=name,
        parent_id=parent_id,
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
    prompt_tracking_instrumentation_method=None,
    prompt_multimodal=None,
    is_decorator=False,
    name=None,
    parent_id=None,
):
    expected_tags = _expected_llmobs_tags(
        span, tags=tags, error=error, session_id=session_id, is_decorator=is_decorator
    )
    if prompt_tracking_instrumentation_method:
        expected_tags.append(f"prompt_tracking_instrumentation_method:{prompt_tracking_instrumentation_method}")
    if prompt_multimodal:
        expected_tags.append(f"prompt_multimodal:{prompt_multimodal}")
    expected_tags = sorted(expected_tags)
    if parent_id is None:
        llmobs_parent = _get_nearest_llmobs_ancestor(span)
        parent_id = str(llmobs_parent.span_id) if llmobs_parent else ROOT_PARENT_ID
    span_name = name or span.name
    if span_name in _STANDARD_INTEGRATION_SPAN_NAMES and span.resource != "":
        span_name = span.resource
    span_event = {
        "trace_id": mock.ANY,
        "span_id": str(span.span_id),
        "parent_id": parent_id,
        "name": span_name,
        "start_ns": span.start_ns,
        "duration": span.duration_ns,
        "status": "error" if error else "ok",
        "meta": _Meta(span=_SpanField(kind=span_kind)),
        "metrics": {},
        "tags": expected_tags,
        "_dd": {
            "span_id": str(span.span_id),
            "trace_id": format_trace_id(span.trace_id),
            "apm_trace_id": format_trace_id(span.trace_id),
        },
    }
    if session_id:
        span_event["session_id"] = session_id
    if error:
        span_event["meta"]["error"] = _ErrorField(type=error, message=error_message or "", stack=error_stack or "")
    if span_links:
        span_event["span_links"] = mock.ANY
    return span_event


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
    boolean_value=None,
    tags=None,
    metadata=None,
    assessment=None,
    reasoning=None,
    eval_scope="span",
):
    eval_metric_event = {
        "join_on": {},
        "metric_type": metric_type,
        "label": label,
        "tags": [
            "ddtrace.version:{}".format(ddtrace.__version__),
            "ml_app:{}".format(ml_app if ml_app is not None else "unnamed-ml-app"),
        ],
        "eval_scope": eval_scope,
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
    if boolean_value is not None:
        eval_metric_event["boolean_value"] = boolean_value
    if tags is not None:
        eval_metric_event["tags"] = tags
    if assessment is not None:
        eval_metric_event["assessment"] = assessment
    if reasoning is not None:
        eval_metric_event["reasoning"] = reasoning
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
            "span": {
                "kind": "llm",
            },
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
            "span": {
                "kind": "llm",
            },
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
            "span": {
                "kind": "llm",
            },
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
            "span": {
                "kind": "llm",
            },
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
                        "content": "A" * 2_000_000,
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
            "span": {
                "kind": "llm",
            },
            "model_name": "gpt-3.5-turbo",
            "model_provider": "openai",
            "input": {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an evil dark lord looking for his one ring to rule them all",
                    },
                    {"role": "user", "content": "A" * 2_600_000},
                ],
                "parameters": {"temperature": 0.9, "max_tokens": 256},
            },
            "output": {
                "messages": [
                    {
                        "content": "A" * 2_600_000,
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
            "span": {
                "kind": "workflow",
            },
            "input": {"value": "A" * 2_600_000},
            "output": {"value": "A" * 2_600_000},
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
            "span": {
                "kind": "retrieval",
            },
            "input": {"documents": {"content": "A" * 2_600_000}},
            "output": {"value": "A" * 2_600_000},
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
            span=span,
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
        eval_scope="span",
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
                "span": {
                    "kind": "workflow",
                },
                "input": {"value": mock.ANY},
                "output": {"value": "1.0"},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {
                    "kind": "workflow",
                },
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {
                    "kind": "workflow",
                },
                "input": {"value": mock.ANY},
                "output": {"value": "1.0"},
                "metadata": {
                    "statements": mock.ANY,
                    "faithfulness_list": mock.ANY,
                },
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {
                    "kind": "workflow",
                },
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {
                    "kind": "workflow",
                },
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
            "meta": {"span": {"kind": "task"}, "metadata": {}},
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {
                    "kind": "workflow",
                },
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
            "meta": {"span": {"kind": "task"}, "metadata": {}},
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {"kind": "task"},
                "output": {"value": "1.0"},
                "metadata": {"faithful_statements": 1, "num_statements": 1},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {"kind": "workflow"},
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {"answer_classifications": mock.ANY, "strictness": mock.ANY},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {"kind": "workflow"},
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
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
                "span": {"kind": "workflow"},
                "input": {"value": mock.ANY},
                "output": {"value": mock.ANY},
                "metadata": {},
            },
            "metrics": {},
            "tags": expected_ragas_trace_tags(),
            "_dd": {"span_id": mock.ANY, "trace_id": mock.ANY, "apm_trace_id": mock.ANY},
        },
    ]


def _expected_span_link(span_event, link_from, link_to):
    return {
        "trace_id": span_event["trace_id"],
        "span_id": span_event["span_id"],
        "attributes": {"from": link_from, "to": link_to},
    }


class TestLLMObsSpanWriter(LLMObsSpanWriter):
    __test__ = False

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


def iterate_stream(stream):
    for _ in stream:
        pass


async def aiterate_stream(stream):
    async for _ in stream:
        pass


def next_stream(stream):
    while True:
        try:
            next(stream)
        except StopIteration:
            break


async def anext_stream(stream):
    while True:
        try:
            await stream.__anext__()
        except StopAsyncIteration:
            break


def assert_llmobs_span_data(
    actual,
    *,
    span_kind,
    name=None,
    parent_id=None,
    model_name=None,
    model_provider=None,
    input_messages=None,
    input_value=None,
    output_messages=None,
    output_value=None,
    input_documents=None,
    output_documents=None,
    error=None,
    tool_definitions=None,
    metadata=None,
    tags=None,
    metrics=None,
):
    """Assert against an LLMObsSpanData payload from ``meta_struct['_llmobs']``.

    Structural fields (``span_kind``, ``name``, ``parent_id``, ``model_name``,
    ``model_provider``, input/output messages/values/documents,
    ``tool_definitions``) are strict-equality, checked only when provided.

    ``error`` defaults to asserting no error payload is present. Pass
    ``error=mock.ANY`` to skip the check.

    ``metadata``, ``tags``, ``metrics`` are top-level subset only: declared
    top-level keys must equal exactly, extras tolerated. Nested values compare
    via ``==`` (no recursion) — pinning ``metadata={"_dd": {...}}`` requires
    the full ``_dd`` dict to match. The ``_dd`` block lives under
    ``metadata._dd``; pass via ``metadata``.
    """
    # If meta_struct is empty there's nothing else to assert against; fail fast with a
    # clear hint about the most common cause.
    assert actual, "expected LLMObsSpanData on span, got {!r} (was meta_struct scrubbed?)".format(actual)

    actual_meta = actual.get(LLMOBS_STRUCT.META, {})
    actual_input = actual_meta.get(LLMOBS_STRUCT.INPUT, {})
    actual_output = actual_meta.get(LLMOBS_STRUCT.OUTPUT, {})

    failures = []

    def _normalize_messages(msgs):
        """Default ``role`` to ``""`` on any message dict that omits it.

        SDKs default the role to ``""`` at projection time when it isn't explicitly set
        (matches the existing ``_expected_llmobs_llm_span_event`` helper). Normalizing
        both sides of the comparison keeps tests writable without forcing every entry to
        spell out ``"role": ""``.
        """
        if not isinstance(msgs, list):
            return msgs
        out = []
        for m in msgs:
            if isinstance(m, dict) and m.get("role") is None:
                out.append({**m, "role": ""})
            else:
                out.append(m)
        return out

    def _check_eq(label, expected_value, actual_value):
        if actual_value != expected_value:
            failures.append(
                "{} mismatch:\n    expected={!r}\n    actual={!r}".format(label, expected_value, actual_value)
            )

    def _check_subset(label, expected_subset, actual_dict):
        if not expected_subset.items() <= actual_dict.items():
            failures.append(
                "{} subset mismatch:\n    expected={!r}\n    actual={!r}".format(label, expected_subset, actual_dict)
            )

    # Structural — strict equality on each declared field.
    _check_eq("span.kind", span_kind, actual_meta.get(LLMOBS_STRUCT.SPAN, {}).get(LLMOBS_STRUCT.KIND))
    if name is not None:
        _check_eq("name", name, actual.get(LLMOBS_STRUCT.NAME))
    if parent_id is not None:
        _check_eq("parent_id", parent_id, actual.get(LLMOBS_STRUCT.PARENT_ID))
    if model_name is not None:
        _check_eq("meta.model_name", model_name, actual_meta.get(LLMOBS_STRUCT.MODEL_NAME))
    if model_provider is not None:
        _check_eq("meta.model_provider", model_provider, actual_meta.get(LLMOBS_STRUCT.MODEL_PROVIDER))
    if input_messages is not None:
        _check_eq(
            "meta.input.messages",
            _normalize_messages(input_messages),
            _normalize_messages(actual_input.get(LLMOBS_STRUCT.MESSAGES)),
        )
    if input_value is not None:
        _check_eq("meta.input.value", input_value, actual_input.get(LLMOBS_STRUCT.VALUE))
    if input_documents is not None:
        _check_eq("meta.input.documents", input_documents, actual_input.get(LLMOBS_STRUCT.DOCUMENTS))
    if output_messages is not None:
        _check_eq(
            "meta.output.messages",
            _normalize_messages(output_messages),
            _normalize_messages(actual_output.get(LLMOBS_STRUCT.MESSAGES)),
        )
    if output_value is not None:
        _check_eq("meta.output.value", output_value, actual_output.get(LLMOBS_STRUCT.VALUE))
    if output_documents is not None:
        _check_eq("meta.output.documents", output_documents, actual_output.get(LLMOBS_STRUCT.DOCUMENTS))
    if error is None:
        actual_error = actual_meta.get(LLMOBS_STRUCT.ERROR)
        if actual_error:
            failures.append(
                "meta.error unexpectedly present:\n    expected=<absent>\n    actual={!r}".format(actual_error)
            )
    else:
        _check_eq("meta.error", error, actual_meta.get(LLMOBS_STRUCT.ERROR))
    if tool_definitions is not None:
        _check_eq("meta.tool_definitions", tool_definitions, actual_meta.get(LLMOBS_STRUCT.TOOL_DEFINITIONS))

    # Subset (shallow) — extra keys tolerated, declared keys must match exactly.
    if metadata is not None:
        _check_subset("meta.metadata", metadata, actual_meta.get(LLMOBS_STRUCT.METADATA, {}))
    if tags is not None:
        _check_subset("tags", tags, actual.get(LLMOBS_STRUCT.TAGS, {}))
    if metrics is not None:
        _check_subset("metrics", metrics, actual.get(LLMOBS_STRUCT.METRICS, {}))

    if failures:
        raise AssertionError(
            "assert_llmobs_span_data found {} mismatch(es):\n  - {}".format(len(failures), "\n  - ".join(failures))
        )
