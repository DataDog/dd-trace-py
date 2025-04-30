import json
import os

import vcr


CASETTE_EXTENSION = ".yaml"


# VCR is used to capture and store network requests made to Anthropic.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
def get_request_vcr(ignore_localhost=True):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        ignore_localhost=ignore_localhost,
    )


# Get the name of the cassette to use for a given test
# All LiteLLM requests that use Open AI get routed to the chat completions endpoint,
# so we can reuse the same cassette for each combination of stream and n
def get_cassette_name(stream, n, include_usage=True, tools=False, proxy=False):
    stream_suffix = "_stream" if stream else ""
    choice_suffix = "_multiple_choices" if n > 1 else ""
    # include_usage only affects streamed responses
    if stream and not include_usage:
        usage_suffix = "_exclude_usage"
    else:
        usage_suffix = ""
    tools_suffix = "_with_tools" if tools else ""
    proxy_suffix = "_proxy" if proxy else ""
    return "completion" + stream_suffix + choice_suffix + usage_suffix + tools_suffix + proxy_suffix + CASETTE_EXTENSION


def consume_stream(resp, n, is_completion=False):
    output_messages = [{"content": "", "tool_calls": []} for _ in range(n)]
    token_metrics = {}
    role = None
    for chunk in resp:
        output_messages, token_metrics, role = extract_output_from_chunk(
            chunk, output_messages, token_metrics, role, is_completion
        )
    output_messages = parse_tool_calls(output_messages)
    return output_messages, token_metrics


async def async_consume_stream(resp, n, is_completion=False):
    output_messages = [{"content": "", "tool_calls": []} for _ in range(n)]
    token_metrics = {}
    role = None
    async for chunk in resp:
        output_messages, token_metrics, role = extract_output_from_chunk(
            chunk, output_messages, token_metrics, role, is_completion
        )
    output_messages = parse_tool_calls(output_messages)
    return output_messages, token_metrics


def extract_output_from_chunk(chunk, output_messages, token_metrics, role, is_completion=False):
    for choice in chunk["choices"]:
        content = choice["text"] if is_completion else choice["delta"]["content"]
        content = content or ""
        output_messages[choice.index]["content"] += content
        if "role" not in output_messages[choice.index] and (choice.get("delta", {}).get("role") or role):
            role = choice.get("delta", {}).get("role") or role
            output_messages[choice.index]["role"] = role
        if choice.get("delta", {}).get("tool_calls", []):
            tool_calls_chunk = choice["delta"]["tool_calls"]
            for tool_call in tool_calls_chunk:
                while tool_call.index >= len(output_messages[choice.index]["tool_calls"]):
                    output_messages[choice.index]["tool_calls"].append({})
                arguments = output_messages[choice.index]["tool_calls"][tool_call.index].get("arguments", "")
                output_messages[choice.index]["tool_calls"][tool_call.index]["name"] = (
                    output_messages[choice.index]["tool_calls"][tool_call.index].get("name", None)
                    or tool_call.function.name
                )
                output_messages[choice.index]["tool_calls"][tool_call.index]["arguments"] = (
                    arguments + tool_call.function.arguments
                )
                output_messages[choice.index]["tool_calls"][tool_call.index]["tool_id"] = (
                    output_messages[choice.index]["tool_calls"][tool_call.index].get("tool_id", None) or tool_call.id
                )
                output_messages[choice.index]["tool_calls"][tool_call.index]["type"] = tool_call.type

    if "usage" in chunk and chunk["usage"]:
        token_metrics.update(
            {
                "input_tokens": chunk["usage"]["prompt_tokens"],
                "output_tokens": chunk["usage"]["completion_tokens"],
                "total_tokens": chunk["usage"]["total_tokens"],
            }
        )

    return output_messages, token_metrics, role


def parse_tool_calls(output_messages):
    # remove tool_calls from messages if they are empty and parse arguments
    for message in output_messages:
        if message["tool_calls"]:
            for tool_call in message["tool_calls"]:
                if "arguments" in tool_call:
                    tool_call["arguments"] = json.loads(tool_call["arguments"])
        else:
            del message["tool_calls"]
    return output_messages


def parse_response(resp, is_completion=False):
    output_messages = []
    for choice in resp.choices:
        content = choice.text if is_completion else choice.message.content
        message = {"content": content or ""}
        if choice.get("role", None) or choice.get("message", {}).get("role", None):
            message["role"] = choice["role"] if is_completion else choice["message"]["role"]
        tool_calls = choice.get("message", {}).get("tool_calls", [])
        if tool_calls:
            message["tool_calls"] = []
            for tool_call in tool_calls:
                message["tool_calls"].append(
                    {
                        "name": tool_call["function"]["name"],
                        "arguments": json.loads(tool_call["function"]["arguments"]),
                        "tool_id": tool_call["id"],
                        "type": tool_call["type"],
                    }
                )
        output_messages.append(message)
    token_metrics = {
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
        "total_tokens": resp.usage.total_tokens,
    }
    return output_messages, token_metrics


tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Get the current weather in a given location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA",
                    },
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
            },
        },
    }
]
