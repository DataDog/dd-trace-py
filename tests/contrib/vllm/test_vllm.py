import pytest

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

    llm = vllm.LLM(
        model="facebook/opt-125m",
        gpu_memory_utilization=0.05,
        max_model_len=512,
        enforce_eager=True,
        compilation_config=0,
    )
    sampling = vllm.SamplingParams(temperature=0.1, top_p=0.9, max_tokens=8)
    outputs = llm.generate(prompts, sampling)


    #assert len(outputs) == len(prompts)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_chat(vllm, vllm_engine_mode):
    llm = vllm.LLM(
        model="facebook/opt-125m",
        gpu_memory_utilization=0.05,
        max_model_len=512,
        enforce_eager=True,
        compilation_config=0,
    )
    sampling_params = llm.get_default_sampling_params()

    conversation = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello! How can I assist you today?"},
        {
            "role": "user", 
            "content": "Write an essay about the importance of higher education."
        },
    ]

    # Transformers >=4.44 requires an explicit chat template if the tokenizer doesn't define one (e.g., OPT)
    simple_chat_template = (
        "{% for message in messages %}"
        "{% if message['role'] == 'system' %}{{ message['content'] }}\n"
        "{% elif message['role'] == 'user' %}User: {{ message['content'] }}\n"
        "{% elif message['role'] == 'assistant' %}Assistant: {{ message['content'] }}\n"
        "{% endif %}"
        "{% endfor %}"
        "Assistant:"
    )
    outputs = llm.chat(conversation, sampling_params, chat_template=simple_chat_template, use_tqdm=False)
    #assert len(outputs) == 1


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_classify(vllm, vllm_engine_mode):
    llm = vllm.LLM(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        gpu_memory_utilization=0.05,
        max_model_len=512,
        enforce_eager=True,
        compilation_config=0,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    outputs = llm.classify(prompts)
    #assert len(outputs) == len(prompts)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_embed(vllm, vllm_engine_mode):

    llm = vllm.LLM(
        model="intfloat/e5-small",
        runner="pooling",
        gpu_memory_utilization=0.05,
        enforce_eager=True,
        compilation_config=0,
        max_model_len=512,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    outputs = llm.embed(prompts)


@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_reward(vllm, vllm_engine_mode):

    llm = vllm.LLM(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        gpu_memory_utilization=0.05,
        enforce_eager=True,
        compilation_config=0,
        max_model_len=512,
        trust_remote_code=True,
    )

    prompts = [
        "Hello, my name is",
        "The capital of France is",
    ]

    outputs = llm.reward(prompts)
    #assert len(outputs) == len(prompts)

@pytest.mark.snapshot(ignores=IGNORE_FIELDS)
def test_score(vllm, vllm_engine_mode):

    llm = vllm.LLM(
        model="BAAI/bge-reranker-v2-m3",
        runner="pooling",
        gpu_memory_utilization=0.05,
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

    outputs = llm.score(text_1, texts_2)
    #assert len(outputs) == len(texts_2)