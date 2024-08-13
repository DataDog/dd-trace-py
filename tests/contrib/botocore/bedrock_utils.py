import os


try:
    import vcr
except ImportError:
    vcr = None
    get_request_vcr = None


_MODELS = {
    "ai21": "ai21.j2-mid-v1",
    "amazon": "amazon.titan-tg1-large",
    "anthropic": "anthropic.claude-instant-v1",
    "anthropic_message": "anthropic.claude-3-sonnet-20240229-v1:0",
    "cohere": "cohere.command-light-text-v14",
    "meta": "meta.llama2-13b-chat-v1",
}

_REQUEST_BODIES = {
    "ai21": {
        "prompt": "Explain like I'm a five-year old: what is a neural network?",
        "temperature": 0.9,
        "topP": 1.0,
        "maxTokens": 10,
        "stopSequences": [],
    },
    "amazon": {
        "inputText": "Command: can you explain what Datadog is to someone not in the tech industry?",
        "textGenerationConfig": {"maxTokenCount": 50, "stopSequences": [], "temperature": 0, "topP": 0.9},
    },
    "anthropic": {
        "prompt": "\n\nHuman: %s\n\nAssistant: What makes you better than Chat-GPT or LLAMA?",
        "temperature": 0.9,
        "top_p": 1,
        "top_k": 250,
        "max_tokens_to_sample": 50,
        "stop_sequences": ["\n\nHuman:"],
    },
    "anthropic_message": {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "summarize the plot to the lord of the rings in a dozen words"}],
            }
        ],
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 50,
        "temperature": 0,
    },
    "cohere": {
        "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
        "temperature": 0.9,
        "p": 1.0,
        "k": 0,
        "max_tokens": 10,
        "stop_sequences": [],
        "stream": False,
        "num_generations": 1,
    },
    "meta": {
        "prompt": "What does 'lorem ipsum' mean?",
        "temperature": 0.9,
        "top_p": 1.0,
        "max_gen_len": 60,
    },
}


# VCR is used to capture and store network requests made to OpenAI and other APIs.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
# To (re)-generate the cassettes: pass a real API key with
# {PROVIDER}_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "bedrock_cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "X-Amz-Security-Token"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
