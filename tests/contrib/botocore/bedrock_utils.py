from io import BytesIO
import os

import boto3
import botocore
import urllib3


try:
    import vcr
except ImportError:
    vcr = None
    get_request_vcr = None

from ddtrace.internal.utils.version import parse_version


BOTO_VERSION = parse_version(boto3.__version__)

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

_MOCK_AI21_RESPONSE_DATA = (
    b'{"completions": [{"data": {"text": "A LLM chain is like a relay race where each AI model passes '
    b'information to the next one to solve complex tasks together, similar to how a team of experts '
    b'work together to solve a problem!"}}]}'
)

_MOCK_AMAZON_RESPONSE_DATA = (
    b'{"inputTextTokenCount": 10, "results": [{"tokenCount": 35, "outputText": "A LLM chain is a sequence of AI models '
    b'working together, where each model builds upon the previous one\'s output to solve complex tasks.", '
    b'"completionReason": "FINISH"}]}'
)

_MOCK_AMAZON_STREAM_RESPONSE_DATA = (
    b'{"outputText": "A LLM chain is a sequence of AI models working together, where each model builds '
    b'upon the previous one\'s output to solve complex tasks.", "completionReason": "FINISH"}'
)

_MOCK_ANTHROPIC_RESPONSE_DATA = (
    b'{"completion": "A LLM chain is a sequence of AI models working together, where each model builds '
    b'upon the previous one\'s output to solve complex tasks.", "stop_reason": "stop_sequence"}'
)

_MOCK_ANTHROPIC_MESSAGE_RESPONSE_DATA = (
    b'{"content": "A LLM chain is a sequence of AI models working together, where each model builds '
    b'upon the previous one\'s output to solve complex tasks.", "stop_reason": "max_tokens"}'
)

_MOCK_COHERE_RESPONSE_DATA = (
    b'{"generations": [{"text": "A LLM chain is a sequence of AI models working together, where each model builds '
    b'upon the previous one\'s output to solve complex tasks", '
    b'"finish_reason": "MAX_TOKENS", "id": "e9b9cff2-2404-4bc2-82ec-e18c424849f7"}]}'
)

_MOCK_COHERE_STREAM_RESPONSE_DATA = (
    b'{"generations": [{"text": "A LLM chain is a sequence of AI models working together, where each model builds '
    b'upon the previous one\'s output to solve complex tasks"}], '
    b'"is_finished": true, "finish_reason": "MAX_TOKENS"}'
)

_MOCK_META_RESPONSE_DATA = (
    b'{"generation": "A LLM chain is a sequence of AI models working together, where each model builds '
    b'upon the previous one\'s output to solve complex tasks.", '
    b'"stop_reason": "max_tokens"}'
)

_RESPONSE_BODIES = {
    "stream": {
        "ai21": _MOCK_AI21_RESPONSE_DATA,
        "amazon": _MOCK_AMAZON_STREAM_RESPONSE_DATA,
        "anthropic": _MOCK_ANTHROPIC_RESPONSE_DATA,
        "anthropic_message": _MOCK_ANTHROPIC_MESSAGE_RESPONSE_DATA,
        "cohere": _MOCK_COHERE_STREAM_RESPONSE_DATA,
        "meta": _MOCK_META_RESPONSE_DATA,
    },
    "non_stream": {
        "ai21": _MOCK_AI21_RESPONSE_DATA,
        "amazon": _MOCK_AMAZON_RESPONSE_DATA,
        "anthropic": _MOCK_ANTHROPIC_RESPONSE_DATA,
        "anthropic_message": _MOCK_ANTHROPIC_MESSAGE_RESPONSE_DATA,
        "cohere": _MOCK_COHERE_RESPONSE_DATA,
        "meta": _MOCK_META_RESPONSE_DATA,
    },
}


class MockStream:
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        yield {"chunk": {"bytes": self.response}}


def get_mock_response_data(provider, stream=False):
    response = _RESPONSE_BODIES["stream" if stream else "non_stream"][provider]
    if stream:
        body = MockStream(response)
    else:
        response_len = len(response)
        body = botocore.response.StreamingBody(
            urllib3.response.HTTPResponse(
                body=BytesIO(response),
                status=200,
                headers={"Content-Type": "application/json"},
                preload_content=False,
            ),
            response_len,
        )

    return {
        "ResponseMetadata": {
            "RequestId": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "date": "Wed, 05 Mar 2025 18:13:31 GMT",
                "content-type": "application/json",
                "content-length": "285",
                "connection": "keep-alive",
                "x-amzn-requestid": "fddf10b3-c895-4e5d-9b21-3ca963708b03",
                "x-amzn-bedrock-invocation-latency": "2823",
                "x-amzn-bedrock-output-token-count": "91",
                "x-amzn-bedrock-input-token-count": "10",
            },
            "RetryAttempts": 0,
        },
        "contentType": "application/json",
        "body": body,
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
