import json

import pytest

from tests.contrib.mistralai.utils import CHAT_TOOLS
from tests.contrib.mistralai.utils import FULL_CHAT_REQUEST_KWARGS
from tests.contrib.mistralai.utils import FULL_EMBED_REQUEST_KWARGS
from tests.contrib.mistralai.utils import get_weather
from tests.utils import override_global_config


def test_global_tags(mistral_client, test_spans):
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        mistral_client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            **FULL_CHAT_REQUEST_KWARGS,
        )

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "Chat.complete"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"


def test_mistralai_chat_complete(mistral_client, snapshot_context):
    with snapshot_context(token="tests.contrib.mistralai.test_mistralai.test_mistralai_chat_complete"):
        mistral_client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            **FULL_CHAT_REQUEST_KWARGS,
        )


def test_mistralai_chat_complete_error(mistral_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.mistralai.test_mistralai.test_mistralai_chat_complete_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            mistral_client.chat.complete(
                model="mistral-large-latest",
                messages=[{"role": "user", "content": "Why is the sky blue?"}],
                not_a_real_argument="this should fail",
            )


async def test_mistralai_chat_complete_async(mistral_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.mistralai.test_mistralai.test_mistralai_chat_complete",
        ignores=["resource"],
    ):
        await mistral_client.chat.complete_async(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "Why is the sky blue?"}],
            **FULL_CHAT_REQUEST_KWARGS,
        )


async def test_mistralai_chat_complete_async_error(mistral_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.mistralai.test_mistralai.test_mistralai_chat_complete_async_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            await mistral_client.chat.complete_async(
                model="mistral-large-latest",
                messages=[{"role": "user", "content": "Why is the sky blue?"}],
                not_a_real_argument="this should fail",
            )


def test_mistralai_chat_complete_with_tools(mistral_client, snapshot_context):
    token = "tests.contrib.mistralai.test_mistralai.test_mistralai_chat_complete_with_tools_two_turn"
    with snapshot_context(token=token):
        resp = mistral_client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": "What's the weather in NYC?"}],
            tools=CHAT_TOOLS,
            **FULL_CHAT_REQUEST_KWARGS,
        )

        assert resp.choices[0].message.tool_calls[0].function.name == "get_weather"
        assert "New York" in resp.choices[0].message.tool_calls[0].function.arguments

        function_result = get_weather(resp.choices[0].message.tool_calls[0].function.arguments)

        final_resp = mistral_client.chat.complete(
            model="mistral-large-latest",
            messages=[
                {"role": "user", "content": "What's the weather in NYC?"},
                resp.choices[0].message,
                {
                    "role": "tool",
                    "content": json.dumps(function_result),
                    "tool_call_id": resp.choices[0].message.tool_calls[0].id,
                },
            ],
            tools=CHAT_TOOLS,
            **FULL_CHAT_REQUEST_KWARGS,
        )

        assert final_resp.choices[0].message.content
        assert "72" in final_resp.choices[0].message.content
        assert "New York" in final_resp.choices[0].message.content


def test_mistralai_embed_create(mistral_client, snapshot_context):
    with snapshot_context(token="tests.contrib.mistralai.test_mistralai.test_mistralai_embed_create"):
        mistral_client.embeddings.create(
            model="mistral-embed",
            inputs=["Why is the sky blue?", "What is your age?"],
            **FULL_EMBED_REQUEST_KWARGS,
        )


def test_mistralai_embed_create_error(mistral_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.mistralai.test_mistralai.test_mistralai_embed_create_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            mistral_client.embeddings.create(
                model="mistral-embed",
                inputs=["Why is the sky blue?"],
                not_a_real_argument="this should fail",
            )


async def test_mistralai_embed_create_async(mistral_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.mistralai.test_mistralai.test_mistralai_embed_create",
        ignores=["resource"],
    ):
        await mistral_client.embeddings.create_async(
            model="mistral-embed",
            inputs=["Why is the sky blue?", "What is your age?"],
            **FULL_EMBED_REQUEST_KWARGS,
        )


async def test_mistralai_embed_create_async_error(mistral_client, snapshot_context):
    with snapshot_context(
        token="tests.contrib.mistralai.test_mistralai.test_mistralai_embed_create_async_error",
        ignores=["meta.error.stack", "meta.error.message"],
    ):
        with pytest.raises(TypeError):
            await mistral_client.embeddings.create_async(
                model="mistral-embed",
                inputs=["Why is the sky blue?"],
                not_a_real_argument="this should fail",
            )
