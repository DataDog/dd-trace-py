import asyncio

import mock
import pytest
from vllm.sampling_params import RequestOutputKind

from tests.llmobs._utils import _expected_llmobs_llm_span_event

from ._utils import create_async_engine
from ._utils import get_llm


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
]

@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_basic(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    llm = get_llm(
        model="facebook/opt-125m",
        engine_mode=vllm_engine_mode,
        max_model_len=256,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
    )
    sampling = vllm.SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8, seed=42)
    llm.generate("The future of AI is", sampling)
    span = mock_tracer.pop_traces()[0][0]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPAN---")
    print(span)
    print("---END SPAN---")
    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[{"content": "The future of AI is", "role": ""}],
        output_messages=[{"content": " in the hands of the people.\n", "role": ""}],
        metadata={
            "max_tokens": 8,
            "presence_penalty": 0.0,
            "n": 1,
            "repetition_penalty": 1.0,
            "temperature": 0.1,
            "top_k": 0,
            "frequency_penalty": 0.0,
            "top_p": 0.9,
            "finish_reason": "length",
            "seed": 42,
        }
        | ({"num_cached_tokens": 0} if vllm_engine_mode == "1" else {}),
        token_metrics={"input_tokens": 6, "output_tokens": 8, "total_tokens": 14},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_chat(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    llm = get_llm(
        model="facebook/opt-125m",
        engine_mode=vllm_engine_mode,
        max_model_len=256,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
    )
    sampling_params = vllm.SamplingParams(seed=42)

    conversation = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I assist you today?"},
        {"role": "user", "content": "Write an essay about the importance of higher education."},
    ]

    simple_chat_template = (
        "{% for message in messages %}"
        "{% if message['role'] == 'system' %}{{ message['content'] }}\n"
        "{% elif message['role'] == 'user' %}User: {{ message['content'] }}\n"
        "{% elif message['role'] == 'assistant' %}Assistant: {{ message['content'] }}\n"
        "{% endif %}"
        "{% endfor %}"
        "Assistant:"
    )

    llm.chat(conversation, sampling_params, chat_template=simple_chat_template, use_tqdm=False)
    span = mock_tracer.pop_traces()[0][0]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPAN---")
    print(span)
    print("---END SPAN---")

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[
            {
                "content": (
                    "You are a helpful assistant\nUser: Hello\nAssistant: Hello! How can I assist you today?\n"
                    "User: Write an essay about the importance of higher education.\nAssistant:"
                ),
                "role": "",
            }
        ],
        output_messages=[
            {
                "content": (
                    " Provide lecture information about INTERESTED universities by translating people's "
                    "ideas into their letters"
                ),
                "role": "",
            }
        ],
        metadata={
            "seed": 42,
            "repetition_penalty": 1.0,
            "max_tokens": 16,
            "top_k": 0,
            "temperature": 1.0,
            "presence_penalty": 0.0,
            "top_p": 1.0,
            "n": 1,
            "frequency_penalty": 0.0,
            "finish_reason": "length",
        }
        | ({"num_cached_tokens": mock.ANY} if vllm_engine_mode == "1" else {}),
        token_metrics={"input_tokens": 37, "output_tokens": 16, "total_tokens": 53},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_classify(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    llm = get_llm(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        max_model_len=256,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.classify(prompts)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[0, 35378, 4, 759, 9351, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
        "[0, 581, 10323, 111, 9942, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
    }

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        token_text = event["meta"]["input"]["documents"][0]["text"]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_stream_cancel_early_break_v1(vllm, mock_tracer, monkeypatch, llmobs_events):
    monkeypatch.setenv("VLLM_USE_V1", "1")

    engine = create_async_engine(
        model="facebook/opt-125m",
        engine_mode="1",
        enforce_eager=True,
    )

    sampling_params = vllm.SamplingParams(
        max_tokens=128,
        temperature=0.8,
        top_p=0.95,
        seed=42,
        output_kind=RequestOutputKind.DELTA,
    )

    i = 0
    saw_finished = False
    async for out in engine.generate(
        request_id="cancel-v1",
        prompt="What is the capital of France?",
        sampling_params=sampling_params,
    ):
        i += 1
        if getattr(out, "finished", False):
            saw_finished = True
        if i >= 2:
            break

    span = mock_tracer.pop_traces()[0][0]
    print("---SPANS---")
    print(span)
    print("---END SPANS---")
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    assert not saw_finished
    assert span.resource == "vllm.request"

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[{"content": "What is the capital of France?", "role": ""}],
        output_messages=[
            {
                "content": (
                    "\nI think it's a city in the middle of the Greek Pacific Ocean, though. I'm not sure, "
                    "since I don't have the map."
                ),
                "role": "",
            }
        ],
        metadata={
            "temperature": 0.8,
            "top_p": 0.95,
            "top_k": 0,
            "presence_penalty": 0.0,
            "repetition_penalty": 1.0,
            "max_tokens": 128,
            "n": 1,
            "frequency_penalty": 0.0,
            "seed": 42,
            "num_cached_tokens": 0,
            "finish_reason": "stop",
        },
        token_metrics={"input_tokens": 8, "output_tokens": 32, "total_tokens": 40},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected
    # Ensure engine is torn down promptly
    try:
        shutdown = getattr(engine, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        pass


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_stream_cancel_early_break_v0_mq(vllm, mock_tracer, monkeypatch, llmobs_events):
    monkeypatch.setenv("VLLM_USE_V1", "0")
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)

    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.entrypoints.openai.api_server import build_async_engine_client_from_engine_args
    from vllm.usage.usage_lib import UsageContext

    args = AsyncEngineArgs(
        model="facebook/opt-125m",
        enforce_eager=True,
        disable_log_stats=True,
        max_model_len=256,
        max_num_batched_tokens=256,
        max_num_seqs=1,
        compilation_config={"use_inductor": False},
        trust_remote_code=True,
    )
    async with build_async_engine_client_from_engine_args(
        args,
        usage_context=UsageContext.OPENAI_API_SERVER,
    ) as engine:
        sampling_params = vllm.SamplingParams(
            max_tokens=128,
            temperature=0.8,
            top_p=0.95,
            seed=42,
            output_kind=RequestOutputKind.DELTA,
        )

        i = 0
        saw_finished = False
        async for out in engine.generate(
            request_id="cancel-mq",
            prompt="What is the capital of the United States?",
            sampling_params=sampling_params,
        ):
            i += 1
            if getattr(out, "finished", False):
                saw_finished = True
            if i >= 2:
                break

    # Encourage CUDA allocator to release memory between retries
    try:
        import torch  # type: ignore

        if hasattr(torch, "cuda"):
            torch.cuda.empty_cache()
    except Exception:
        pass
    span = mock_tracer.pop_traces()[0][0]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(span)
    print("---END SPANS---")
    assert not saw_finished
    assert span.resource == "vllm.request"

    assert len(llmobs_events) == 1
    expected = _expected_llmobs_llm_span_event(
        span,
        model_name="facebook/opt-125m",
        model_provider="vllm",
        input_messages=[{"content": "What is the capital of the United States?", "role": ""}],
        output_messages=[
            {
                "content": (
                    "\nI think you mean Commonwealth of Virginia.\nWell, I'm not sure that's how it works.  *\n"
                    "I'm talking about New York City.\nYes I'm aware."
                ),
                "role": "",
            }
        ],
        metadata={"num_cached_tokens": 0, "finish_reason": "stop"},
        token_metrics={"input_tokens": 10, "output_tokens": 40, "total_tokens": 50},
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
    )
    assert llmobs_events[0] == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_embed(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    llm = get_llm(
        model="intfloat/e5-small",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
        max_model_len=256,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.embed(prompts)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[101, 7592, 1010, 2026, 2171, 2003, 102]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
        "[101, 1996, 3007, 1997, 2605, 2003, 102]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
    }

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        token_text = event["meta"]["input"]["documents"][0]["text"]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="intfloat/e5-small",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 384]",
            metadata={"embedding_dim": 384},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_reward(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    llm = get_llm(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        compilation_config={"use_inductor": False},
        max_model_len=256,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    res = llm.reward(prompts)
    print("---RES---")
    print(res)
    print("Number of embeddings:", len(res))
    print("Size of each embedding:", len(res[0].outputs.data))
    print("Shape of each embedding:", res[0].outputs.data.shape)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")

    # Expect one event per input prompt
    assert len(llmobs_events) == len(prompts) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[0, 35378, 4, 759, 9351, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
        "[0, 581, 10323, 111, 9942, 83, 2]": {"input_tokens": 7, "output_tokens": 0, "total_tokens": 7},
    }

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        token_text = event["meta"]["input"]["documents"][0]["text"]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[7 embedding(s) returned with size 1024]",
            metadata={"embedding_dim": 1024},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_llmobs_score(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    llm = get_llm(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        max_model_len=256,
        compilation_config={"use_inductor": False},
        trust_remote_code=True,
    )

    text_1 = "What is the capital of France?"
    texts_2 = [
        "The capital of Brazil is Brasilia.",
        "The capital of France is Paris.",
    ]

    llm.score(text_1, texts_2)
    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")

    # Expect one event per candidate document
    assert len(llmobs_events) == len(texts_2) == len(spans)
    span_by_id = {s.span_id: s for s in spans}

    expected_token_metrics_by_text = {
        "[0, 4865, 83, 70, 10323, 111, 9942, 32, 2, 2, 581, 10323, 111, 30089, 83, 8233, 399, 5, 2]": {
            "input_tokens": 19,
            "output_tokens": 0,
            "total_tokens": 19,
        },
        "[0, 4865, 83, 70, 10323, 111, 9942, 32, 2, 2, 581, 10323, 111, 9942, 83, 7270, 5, 2]": {
            "input_tokens": 18,
            "output_tokens": 0,
            "total_tokens": 18,
        },
    }

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        token_text = event["meta"]["input"]["documents"][0]["text"]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="BAAI/bge-reranker-v2-m3",
            model_provider="vllm",
            input_documents=[{"text": token_text}],
            output_value="[1 embedding(s) returned with size 1]",
            metadata={"embedding_dim": 1},
            token_metrics=expected_token_metrics_by_text[token_text],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_llmobs_concurrent_streaming(vllm, llmobs_events, mock_tracer, vllm_engine_mode):
    engine = create_async_engine(
        model="facebook/opt-125m",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        max_model_len=256,
        compilation_config={"use_inductor": False},
        trust_remote_code=True,
    )

    sampling_params = vllm.SamplingParams(
        max_tokens=64,
        temperature=0.8,
        top_p=0.95,
        seed=42,
        output_kind=RequestOutputKind.DELTA,
    )

    async def _run(req_id: str, prompt: str):
        async for out in engine.generate(
            request_id=req_id,
            prompt=prompt,
            sampling_params=sampling_params,
        ):
            if out.finished:
                break

    await asyncio.gather(
        _run("stream-conc-1", "The future of AI is"),
        _run("stream-conc-2", "In a galaxy far, far away"),
    )

    traces = mock_tracer.pop_traces()
    spans = [s for t in traces for s in t]
    print("---LLMOBS EVENTS---")
    print(llmobs_events)
    print("---END LLMOBS EVENTS---")
    print("---SPANS---")
    print(spans)
    print("---END SPANS---")

    # Expect two events, one per prompt
    assert len(llmobs_events) == 2
    span_by_id = {s.span_id: s for s in spans}

    # Build expectations keyed by input content
    expected_by_input = {
        "The future of AI is": {
            "token_metrics": {"input_tokens": 6, "output_tokens": 64, "total_tokens": 70},
            "output_text": (
                " in the minds of scientists\nThe future of AI is in the minds of scientists, and I believe "
                "that's because the tools are being used to drive what's happening in artificial "
                "intelligence. One of the main challenges for AI is how to do something like that. In "
                "order to make that possible, we need to start to"
            ),
        },
        "In a galaxy far, far away": {
            "token_metrics": {"input_tokens": 8, "output_tokens": 64, "total_tokens": 72},
            "output_text": (
                ", there's a new TV show called ‘The Young and the Restless’ which has been adapted into a "
                "movie. It’s called ‘The Young and the Restless’!\n\n‘The Young and the Restless’ was created "
                "by Russell Crowe. The show started"
            ),
        },
    }

    for event in llmobs_events:
        span = span_by_id[int(event["span_id"])]
        # Determine which prompt this event corresponds to
        input_msg = event["meta"]["input"]["messages"][0]["content"]
        exp = expected_by_input[input_msg]

        expected = _expected_llmobs_llm_span_event(
            span,
            model_name="facebook/opt-125m",
            model_provider="vllm",
            input_messages=[{"content": input_msg, "role": ""}],
            output_messages=[{"content": exp["output_text"], "role": ""}],
            metadata={
                "frequency_penalty": 0.0,
                "repetition_penalty": 1.0,
                "presence_penalty": 0.0,
                "max_tokens": 64,
                "top_p": 0.95,
                "n": 1,
                "temperature": 0.8,
                "top_k": 0,
                "seed": 42,
                "finish_reason": "length",
            }
            | ({"num_cached_tokens": 0} if vllm_engine_mode == "1" else {}),
            token_metrics=exp["token_metrics"],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.vllm"},
        )
        assert event == expected
    # Ensure engine is torn down promptly
    try:
        shutdown = getattr(engine, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        pass
