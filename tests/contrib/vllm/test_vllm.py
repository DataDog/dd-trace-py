import pytest
from vllm.sampling_params import RequestOutputKind

from ._utils import create_async_engine
from ._utils import get_cached_llm
from ._utils import get_simple_chat_template


IGNORE_FIELDS = [
    "metrics.vllm.latency.ttft",
    "metrics.vllm.latency.queue",
]


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_basic(vllm, vllm_engine_mode):
    prompts = [
        "Hello, my name is",
        "The president of the United States is",
        "The capital of France is",
        "The future of AI is",
    ]

    llm = get_cached_llm(
        model="facebook/opt-125m",
        engine_mode=vllm_engine_mode,
        max_model_len=512,
        enforce_eager=True,
        compilation_config=0,
    )
    sampling = vllm.SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8)
    llm.generate(prompts, sampling)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_chat(vllm, vllm_engine_mode):
    llm = get_cached_llm(
        model="facebook/opt-125m",
        engine_mode=vllm_engine_mode,
        max_model_len=512,
        enforce_eager=True,
        compilation_config=0,
    )
    sampling_params = llm.get_default_sampling_params()

    conversation = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I assist you today?"},
        {"role": "user", "content": "Write an essay about the importance of higher education."},
    ]

    # Transformers >=4.44 requires an explicit chat template for models without one (e.g., OPT)
    simple_chat_template = get_simple_chat_template()
    llm.chat(conversation, sampling_params, chat_template=simple_chat_template, use_tqdm=False)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_classify(vllm, vllm_engine_mode):
    llm = get_cached_llm(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        max_model_len=512,
        enforce_eager=True,
        compilation_config=0,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.classify(prompts)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_embed(vllm, vllm_engine_mode):
    llm = get_cached_llm(
        model="intfloat/e5-small",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        compilation_config=0,
        max_model_len=512,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.embed(prompts)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_reward(vllm, vllm_engine_mode):
    llm = get_cached_llm(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        compilation_config=0,
        max_model_len=512,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    llm.reward(prompts)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_score(vllm, vllm_engine_mode):
    llm = get_cached_llm(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        max_model_len=512,
        compilation_config=0,
        trust_remote_code=True,
    )

    text_1 = "What is the capital of France?"
    texts_2 = [
        "The capital of Brazil is Brasilia.",
        "The capital of France is Paris.",
    ]

    llm.score(text_1, texts_2)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_async_streaming(vllm, vllm_engine_mode):
    engine = create_async_engine(
        model="facebook/opt-125m",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        max_model_len=512,
        compilation_config=0,
        trust_remote_code=True,
    )

    sampling_params = vllm.SamplingParams(
        max_tokens=64,
        temperature=0.8,
        top_p=0.95,
        seed=42,
        output_kind=RequestOutputKind.DELTA,
    )

    async for out in engine.generate(
        request_id="stream-test-1",
        prompt="The future of artificial intelligence is",
        sampling_params=sampling_params,
    ):
        if out.finished:
            break


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
async def test_async_encode_streaming(vllm, vllm_engine_mode):
    engine = create_async_engine(
        model="intfloat/e5-small",
        engine_mode=vllm_engine_mode,
        enforce_eager=True,
        runner="pooling",
        trust_remote_code=True,
        max_model_len=512,
        compilation_config=0,
    )

    params = vllm.PoolingParams(task="encode")
    prompts = ["Hello, my name is", "The capital of France is"]

    for p in prompts:
        async for out in engine.encode(
            request_id=f"encode-{hash(p) & 0xffff}",
            prompt=p,
            pooling_params=params,
        ):
            if out.finished:
                break
