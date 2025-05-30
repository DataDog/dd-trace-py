import os

import boto3


try:
    import vcr
except ImportError:
    vcr = None
    get_request_vcr = None

from ddtrace.internal.utils.version import parse_version


BOTO_VERSION = parse_version(boto3.__version__)

AGENT_ALIAS_ID = "NWGOFQESWP"
AGENT_ID = "EITYAHSOCJ"
AGENT_INPUT = ("I like beach vacations but also nature and outdoor adventures. I'd like the trip to be 7 days, "
                 "and include lounging on the beach, something like an all-inclusive resort is nice too (but I prefer "
                 "luxury 4/5 star resorts)")

bedrock_converse_args_with_system_and_tool = {
    "system": "You are an expert swe that is to use the tool fetch_concept",
    "user_message": "Explain the concept of distributed tracing in a simple way",
    "tools": [
        {
            "toolSpec": {
                "name": "fetch_concept",
                "description": "Fetch an expert explanation for a concept",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"concept": {"type": "string", "description": "The concept to explain"}},
                        "required": ["concept"],
                    }
                },
            }
        },
    ],
}


def create_bedrock_converse_request(user_message=None, tools=None, system=None):
    request_params = {
        "modelId": "anthropic.claude-3-sonnet-20240229-v1:0",
        "messages": [{"role": "user", "content": [{"text": user_message}]}],
        "inferenceConfig": {"temperature": 0.7, "topP": 0.9, "maxTokens": 1000, "stopSequences": []},
    }
    if system:
        request_params["system"] = [{"text": system}]
    if tools:
        request_params["toolConfig"] = {"tools": tools}
    return request_params


_MOCK_RESPONSE_DATA = (
    b'{"inputTextTokenCount": 10, "results": [{"tokenCount": 35, "outputText": "Black '
    b"holes are massive objects that have a gravitational pull so strong that nothing, including light, can "
    b'escape their event horizon. They are formed when very large stars collapse.", '
    b'"completionReason": "FINISH"}]}'
)


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
