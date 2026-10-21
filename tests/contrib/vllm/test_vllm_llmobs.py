from importlib.metadata import version
from unittest import mock

import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_documents
from ddtrace.llmobs.types import Message
from tests.llmobs._utils import assert_llmobs_span_data

from ._tiny_models import HIDDEN_SIZE
from ._tiny_models import TINY_BERT
from ._tiny_models import TINY_OPT
from ._tiny_models import TINY_RERANKER
from ._utils import get_simple_chat_template


VLLM_VERSION = parse_version(version("vllm"))

IGNORE_FIELDS = [
    "meta_struct",
    "meta.llmobs_parent_id",
    "meta.llmobs_trace_id",
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
    "metrics.vllm.latency.prefill",
    "metrics.vllm.latency.decode",
    "metrics.vllm.latency.inference",
    # vLLM reports the model directory, which is a temporary directory, as the model name. The tests check the
    # model name with assert_llmobs_span_data.
    "meta._dd.llmobs.model_name",
    "meta.gen_ai.request.model",
    "meta.vllm.request.model",
]

# The tiny models make one token for each byte. The encoder models add <s> and </s>.
PROMPTS = ["Hello, my name is", "The capital of France is"]
ENCODER_INPUT_TOKENS = {"Hello, my name is": 19, "The capital of France is": 26}


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_basic(vllm_llmobs, test_spans, tiny_opt_llm):
    from vllm import SamplingParams

    llm = tiny_opt_llm
    # The weights are random (load_format="dummy"), so the output text is not fixed. ignore_eos makes the output
    # length fixed.
    sampling = SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8, seed=42, ignore_eos=True)
    llm.generate("The future of AI is", sampling)
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=TINY_OPT,
        model_provider="vllm",
        input_messages=[Message(content="The future of AI is")],
        output_messages=[Message(role="assistant", content=mock.ANY)],
        metadata={
            "max_tokens": 8,
            "n": 1,
            "temperature": 0.1,
            "top_p": 0.9,
            "finish_reason": "length",
            "num_cached_tokens": 0,
        },
        metrics={
            # One token for each byte, plus the </s> BOS token.
            "input_tokens": 20,
            "output_tokens": 8,
            "total_tokens": 28,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
    )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_chat(vllm_llmobs, test_spans, tiny_opt_llm):
    from vllm import SamplingParams

    llm = tiny_opt_llm
    sampling_params = SamplingParams(seed=42, ignore_eos=True)

    conversation = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I assist you today?"},
        {"role": "user", "content": "Write an essay about the importance of higher education."},
    ]

    llm.chat(conversation, sampling_params, chat_template=get_simple_chat_template(), use_tqdm=False)
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1

    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(spans[0]),
        span_kind="llm",
        model_name=TINY_OPT,
        model_provider="vllm",
        input_messages=[
            Message(role="system", content="You are a helpful assistant"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hello! How can I assist you today?"),
            Message(role="user", content="Write an essay about the importance of higher education."),
        ],
        output_messages=[Message(role="assistant", content=mock.ANY)],
        metadata={
            "max_tokens": 16,
            "temperature": 1.0,
            "top_p": 1.0,
            "n": 1,
            "finish_reason": "length",
            "num_cached_tokens": mock.ANY,
        },
        metrics={
            "input_tokens": mock.ANY,
            "output_tokens": 16,
            "total_tokens": mock.ANY,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_decode": mock.ANY,
            "time_in_model_inference": mock.ANY,
        },
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
    )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_classify(vllm_llmobs, test_spans, tiny_reranker_llm):
    llm = tiny_reranker_llm

    prompts = PROMPTS

    llm.classify(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one span per input prompt
    assert len(spans) == len(prompts)

    # Spans may be returned in any order; match by input document text.
    spans_by_prompt = {}
    for span in spans:
        documents = get_llmobs_input_documents(span) or []
        assert len(documents) == 1
        spans_by_prompt[documents[0]["text"]] = span

    assert set(spans_by_prompt.keys()) == set(prompts)

    for prompt in prompts:
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans_by_prompt[prompt]),
            span_kind="embedding",
            model_name=TINY_RERANKER,
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1, "num_cached_tokens": 0},
            metrics={
                "input_tokens": ENCODER_INPUT_TOKENS[prompt],
                "output_tokens": 0,
                "total_tokens": ENCODER_INPUT_TOKENS[prompt],
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_embed(vllm_llmobs, test_spans, tiny_bert_llm):
    llm = tiny_bert_llm

    prompts = PROMPTS

    llm.embed(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    assert len(spans) == len(prompts)

    spans_by_prompt = {}
    for span in spans:
        documents = get_llmobs_input_documents(span) or []
        assert len(documents) == 1
        spans_by_prompt[documents[0]["text"]] = span

    assert set(spans_by_prompt.keys()) == set(prompts)

    for prompt in prompts:
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans_by_prompt[prompt]),
            span_kind="embedding",
            model_name=TINY_BERT,
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value=f"[1 embedding(s) returned with size {HIDDEN_SIZE}]",
            metadata={"embedding_dim": HIDDEN_SIZE, "num_cached_tokens": 0},
            metrics={
                "input_tokens": ENCODER_INPUT_TOKENS[prompt],
                "output_tokens": 0,
                "total_tokens": ENCODER_INPUT_TOKENS[prompt],
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


@pytest.mark.skipif(VLLM_VERSION >= (0, 24, 0), reason="vLLM 0.24.0 removed LLM.reward()")
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_reward(vllm_llmobs, test_spans, tiny_reranker_llm):
    llm = tiny_reranker_llm

    prompts = PROMPTS

    llm.reward(prompts)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    assert len(spans) == len(prompts)

    spans_by_prompt = {}
    for span in spans:
        documents = get_llmobs_input_documents(span) or []
        assert len(documents) == 1
        spans_by_prompt[documents[0]["text"]] = span

    assert set(spans_by_prompt.keys()) == set(prompts)

    for prompt in prompts:
        # vLLM 0.10 returns the hidden states of each token (one vector of size HIDDEN_SIZE for each token). Later
        # versions return one reward score for each token.
        num_tokens = ENCODER_INPUT_TOKENS[prompt]
        if VLLM_VERSION < (0, 11, 0):
            output_value = f"[{num_tokens} embedding(s) returned with size {HIDDEN_SIZE}]"
            embedding_dim = HIDDEN_SIZE
        else:
            output_value, embedding_dim = f"[1 embedding(s) returned with size {num_tokens}]", num_tokens
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans_by_prompt[prompt]),
            span_kind="embedding",
            model_name=TINY_RERANKER,
            model_provider="vllm",
            input_documents=[{"text": prompt}],
            output_value=output_value,
            metadata={"embedding_dim": embedding_dim, "num_cached_tokens": 0},
            metrics={
                "input_tokens": ENCODER_INPUT_TOKENS[prompt],
                "output_tokens": 0,
                "total_tokens": ENCODER_INPUT_TOKENS[prompt],
                "time_to_first_token": mock.ANY,
                "time_in_queue": mock.ANY,
                "time_in_model_prefill": mock.ANY,
                "time_in_model_inference": mock.ANY,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_score(vllm_llmobs, test_spans, tiny_reranker_llm):
    llm = tiny_reranker_llm

    text_1 = "What is the capital of France?"
    texts_2 = [
        "The capital of Brazil is Brasilia.",
        "The capital of France is Paris.",
    ]

    llm.score(text_1, texts_2)
    traces = test_spans.pop_traces()
    spans = [s for t in traces for s in t]

    # Expect one span per candidate document
    assert len(spans) == len(texts_2)

    # The input document of each span is the list of token ids of the (text_1, text_2) pair.
    tokenizer = llm.get_tokenizer()
    input_tokens_by_text_2 = {texts_2[0]: 68, texts_2[1]: 65}
    expected_token_metrics_by_text = {
        str(tokenizer(text_1, text_2).input_ids): {
            "input_tokens": input_tokens,
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "time_to_first_token": mock.ANY,
            "time_in_queue": mock.ANY,
            "time_in_model_prefill": mock.ANY,
            "time_in_model_inference": mock.ANY,
        }
        for text_2, input_tokens in input_tokens_by_text_2.items()
    }

    for span in spans:
        token_text = get_llmobs_input_documents(span)[0]["text"]
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="embedding",
            model_name=TINY_RERANKER,
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1, "num_cached_tokens": 0},
            metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm", "integration": "vllm"},
        )


def test_shadow_tags_completion_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on vLLM spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.contrib.internal.vllm.extractors import RequestData
    from ddtrace.llmobs._integrations.vllm import VLLMIntegration

    integration = VLLMIntegration(MagicMock())

    data = RequestData(input_tokens=20, output_tokens=10)

    with tracer.trace("vllm.request") as span:
        span._set_attribute("vllm.request.model", "meta-llama/Llama-3-8B")
        span._set_attribute("vllm.request.provider", "vllm")
        integration._set_apm_shadow_tags(span, [], {"request_data": data}, response=None, operation="")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "meta-llama/Llama-3-8B"
    assert span.get_tag("_dd.llmobs.model_provider") == "vllm"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 20
    assert span.get_metric("_dd.llmobs.output_tokens") == 10
    assert span.get_metric("_dd.llmobs.total_tokens") == 30
