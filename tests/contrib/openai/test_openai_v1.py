import os

import mock
import openai as openai_module
import pytest

import ddtrace
from ddtrace.contrib.internal.openai.utils import _est_tokens
from ddtrace.internal.utils.version import parse_version
from tests.contrib.openai.utils import chat_completion_custom_functions
from tests.contrib.openai.utils import chat_completion_input_description
from tests.contrib.openai.utils import get_openai_vcr
from tests.contrib.openai.utils import multi_message_input
from tests.utils import override_global_config
from tests.utils import snapshot_context


TIKTOKEN_AVAILABLE = os.getenv("TIKTOKEN_AVAILABLE", False)
pytestmark = pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 0), reason="This module only tests openai >= 1.0"
)


@pytest.fixture(scope="session")
def openai_vcr():
    yield get_openai_vcr(subdirectory_name="v1")


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_model_list(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_list",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("model_list.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.models.list()


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_model_alist(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_list",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("model_alist.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.models.list()


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_model_retrieve(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_retrieve",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("model_retrieve.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.models.retrieve("curie")


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_model_aretrieve(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_retrieve",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("model_retrieve.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.models.retrieve("curie")


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_completion(api_key_in_env, request_api_key, openai, openai_vcr, mock_llmobs_writer, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_completion",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("completion.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            resp = client.completions.create(
                model="ada",
                prompt="Hello world",
                temperature=0.8,
                n=2,
                stop=".",
                max_tokens=10,
                user="ddtrace-test",
            )

    assert resp.object == "text_completion"
    assert resp.model == "ada"
    expected_choices = [
        {"finish_reason": "length", "index": 0, "logprobs": None, "text": ", relax!” I said to my laptop"},
        {"finish_reason": "stop", "index": 1, "logprobs": None, "text": " (1"},
    ]
    for idx, choice in enumerate(resp.choices):
        assert choice.finish_reason == expected_choices[idx]["finish_reason"]
        assert choice.index == expected_choices[idx]["index"]
        assert choice.logprobs == expected_choices[idx]["logprobs"]
        assert choice.text == expected_choices[idx]["text"]

    mock_llmobs_writer.start.assert_not_called()
    mock_llmobs_writer.enqueue.assert_not_called()


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_acompletion(api_key_in_env, request_api_key, openai, openai_vcr, mock_llmobs_writer, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_acompletion",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("completion_async.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            resp = await client.completions.create(
                model="curie",
                prompt="As Descartes said, I think, therefore",
                temperature=0.8,
                n=1,
                max_tokens=150,
                user="ddtrace-test",
            )
    assert resp.object == "text_completion"
    expected_choices = {
        "finish_reason": "length",
        "index": 0,
        "logprobs": None,
        "text": " I am; and I am in a sense a non-human entity woven together from "
        "memories, desires and emotions. But, who is to say that I am not an "
        "artificial intelligence. The brain is a self-organising, "
        "self-aware, virtual reality computer … so how is it, who exactly is "
        "it, this thing that thinks, feels, loves and believes? Are we not "
        "just software running on hardware?\n"
        "\n"
        "Recently, I have come to take a more holistic view of my identity, "
        "not as a series of fleeting moments, but as a long-term, ongoing "
        "process. The key question for me is not that of ‘who am I?’ but "
        "rather, ‘how am I?’ – a question",
    }
    for key, value in expected_choices.items():
        assert getattr(resp.choices[0], key, None) == value

    mock_llmobs_writer.start.assert_not_called()
    mock_llmobs_writer.enqueue.assert_not_called()


def test_global_tags(openai_vcr, openai, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data

    All data should also be tagged with the same OpenAI data.
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with openai_vcr.use_cassette("completion.yaml"):
            client = openai.OpenAI()
            client.completions.create(
                model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
            )

    span = mock_tracer.pop_traces()[0][0]
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("openai.request.model") == "ada"
    assert span.get_tag("openai.request.endpoint") == "/v1/completions"
    assert span.get_tag("openai.request.method") == "POST"
    assert span.get_tag("openai.organization.name") == "datadog-4"
    assert span.get_tag("openai.user.api_key") == "sk-...key>"


def test_completion_raw_response(openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_completion",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("completion.yaml"):
            client = openai.OpenAI()
            client.completions.with_raw_response.create(
                model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
            )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
)
def test_completion_raw_response_stream(openai, openai_vcr, mock_tracer):
    """Assert that no spans are created when streaming and with_raw_response is used."""
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        client = openai.OpenAI()
        client.completions.with_raw_response.create(model="ada", prompt="Hello world", stream=True, n=None)

    assert len(mock_tracer.pop_traces()) == 0


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
)
async def test_acompletion_raw_response_stream(openai, openai_vcr, mock_tracer):
    """Assert that no spans are created when streaming and with_raw_response is used."""
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        client = openai.AsyncOpenAI()
        await client.completions.with_raw_response.create(model="ada", prompt="Hello world", stream=True, n=None)

    assert len(mock_tracer.pop_traces()) == 0


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_chat_completion(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_chat_completion",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("chat_completion.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=multi_message_input,
                top_p=0.9,
                n=2,
                user="ddtrace-test",
            )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_chat_completion_function_calling",
    ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
)
def test_chat_completion_function_calling(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("chat_completion_function_call.yaml"):
        client = openai.OpenAI()
        client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": chat_completion_input_description}],
            functions=chat_completion_custom_functions,
            function_call="auto",
            user="ddtrace-test",
        )


@pytest.mark.skipif(parse_version(openai_module.version.VERSION) < (1, 1), reason="Tool calls available after v1.1.0")
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_chat_completion_function_calling",
    ignores=[
        "meta.http.useragent",
        "meta.openai.api_type",
        "meta.openai.api_base",
        "meta.openai.response.choices.0.finish_reason",
    ],
)
def test_chat_completion_tool_calling(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("chat_completion_tool_call.yaml"):
        client = openai.OpenAI()
        client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": chat_completion_input_description}],
            tools=[{"type": "function", "function": chat_completion_custom_functions[0]}],
            tool_choice="auto",
            user="ddtrace-test",
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_chat_completion_image_input",
    ignores=[
        "meta.http.useragent",
        "meta.openai.api_type",
        "meta.openai.api_base",
    ],
)
def test_chat_completion_image_input(openai, openai_vcr, snapshot_tracer):
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk"
        ".jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
    )
    with openai_vcr.use_cassette("chat_completion_image_input.yaml"):
        client = openai.OpenAI()
        client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What’s in this image?"},
                        {
                            "type": "image_url",
                            "image_url": image_url,
                        },
                    ],
                }
            ],
        )


def test_chat_completion_raw_response(openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_chat_completion",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("chat_completion.yaml"):
            client = openai.OpenAI()
            client.chat.completions.with_raw_response.create(
                model="gpt-3.5-turbo",
                messages=multi_message_input,
                top_p=0.9,
                n=2,
                user="ddtrace-test",
            )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
)
def test_chat_completion_raw_response_stream(openai, openai_vcr, mock_tracer):
    """Assert that no spans are created when streaming and with_raw_response is used."""
    with openai_vcr.use_cassette("chat_completion_streamed_tokens.yaml"):
        client = openai.OpenAI()
        client.chat.completions.with_raw_response.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Who won the world series in 2020?"},
            ],
            stream=True,
            user="ddtrace-test",
            n=None,
        )

    assert len(mock_tracer.pop_traces()) == 0


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
)
async def test_achat_completion_raw_response_stream(openai, openai_vcr, mock_tracer):
    """Assert that no spans are created when streaming and with_raw_response is used."""
    with openai_vcr.use_cassette("chat_completion_streamed_tokens.yaml"):
        client = openai.AsyncOpenAI()
        await client.chat.completions.with_raw_response.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Who won the world series in 2020?"},
            ],
            stream=True,
            user="ddtrace-test",
            n=None,
        )

    assert len(mock_tracer.pop_traces()) == 0


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_achat_completion(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_chat_completion",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta._dd.p.tid"],
    ):
        with openai_vcr.use_cassette("chat_completion_async.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=multi_message_input,
                top_p=0.9,
                n=2,
                user="ddtrace-test",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_image_create(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_create",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("image_create.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.images.generate(
                prompt="sleepy capybara with monkey on top",
                n=1,
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_image_acreate(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_image_create",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("image_create.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.images.generate(
                prompt="sleepy capybara with monkey on top",
                n=1,
                size="256x256",
                response_format="url",
                user="ddtrace-test",
            )


# TODO: Note that vcr tests for image edit/variation don't work as they error out when recording the vcr request,
#  during the payload decoding. We'll need to migrate those tests over once we can address this.
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_image_b64_json_response",
    ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
)
def test_image_b64_json_response(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("image_create_b64_json.yaml"):
        client = openai.OpenAI()
        client.images.generate(
            prompt="sleepy capybara with monkey on top",
            n=1,
            size="256x256",
            response_format="b64_json",
            user="ddtrace-test",
        )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_embedding(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_embedding",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("embedding.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.embeddings.create(input="hello world", model="text-embedding-ada-002", user="ddtrace-test")


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_embedding_string_array",
    ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
)
def test_embedding_string_array(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("embedding_string_array.yaml"):
        client = openai.OpenAI()
        client.embeddings.create(
            input=["hello world", "hello again"], model="text-embedding-ada-002", user="ddtrace-test"
        )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_embedding_token_array",
    ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
)
def test_embedding_token_array(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("embedding_token_array.yaml"):
        client = openai.OpenAI()
        client.embeddings.create(input=[1111, 2222, 3333], model="text-embedding-ada-002", user="ddtrace-test")


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_embedding_array_of_token_arrays",
    ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
)
def test_embedding_array_of_token_arrays(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("embedding_array_of_token_arrays.yaml"):
        client = openai.OpenAI()
        client.embeddings.create(
            input=[[1111, 2222, 3333], [4444, 5555, 6666], [7777, 8888, 9999]],
            model="text-embedding-ada-002",
            user="ddtrace-test",
        )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_aembedding(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_embedding",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("embedding.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.embeddings.create(input="hello world", model="text-embedding-ada-002", user="ddtrace-test")


# TODO: Note that vcr tests for audio transcribe/translate don't work as they error out when recording the vcr request,
#  during the payload decoding. We'll need to migrate those tests over once we can address this.


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_list(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_list",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("file_list.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.files.list()


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_alist(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_list",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("file_list.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.files.list()


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_create(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_create",
        ignores=[
            "meta.http.useragent",
            "meta.openai.api_type",
            "meta.openai.api_base",
            "meta.openai.request.user",
            "meta.openai.request.user_provided_filename",
            "meta.openai.response.filename",
        ],
    ):
        with openai_vcr.use_cassette("file_create.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.files.create(
                file=open(os.path.join(os.path.dirname(__file__), "test_data/training_data.jsonl"), "rb"),
                purpose="fine-tune",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_acreate(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_create",
        ignores=[
            "meta.http.useragent",
            "meta.openai.api_type",
            "meta.openai.api_base",
            "meta.openai.request.user",
            "meta.openai.request.user_provided_filename",
            "meta.openai.response.filename",
        ],
    ):
        with openai_vcr.use_cassette("file_create.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.files.create(
                file=open(os.path.join(os.path.dirname(__file__), "test_data/training_data.jsonl"), "rb"),
                purpose="fine-tune",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_delete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_delete",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("file_delete.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.files.delete(
                file_id="file-l48KgWVF75Tz2HLqLrcUdBPi",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_adelete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_delete",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("file_delete.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.files.delete(
                file_id="file-l48KgWVF75Tz2HLqLrcUdBPi",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_retrieve(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_retrieve",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("file_retrieve.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.files.retrieve(
                file_id="file-Aeh42OWPtbWgt7gfUjXBVFAF",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_aretrieve(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_retrieve",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("file_retrieve.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.files.retrieve(
                file_id="file-Aeh42OWPtbWgt7gfUjXBVFAF",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_file_download(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_download",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("file_download.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.files.retrieve_content(
                file_id="file-xC22NUuYBkXvzRt2fLREcGde",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_file_adownload(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_file_download",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("file_download.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.files.retrieve_content(
                file_id="file-xC22NUuYBkXvzRt2fLREcGde",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_model_delete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_delete",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("model_delete.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.models.delete(
                model="babbage:ft-datadog:dummy-fine-tune-model-2023-06-01-23-15-52",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_model_adelete(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_model_delete",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base", "meta.openai.request.user"],
    ):
        with openai_vcr.use_cassette("model_delete.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.models.delete(
                model="babbage:ft-datadog:dummy-fine-tune-model-2023-06-01-23-15-52",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
def test_create_moderation(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_create_moderation",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("moderation.yaml"):
            client = openai.OpenAI(api_key=request_api_key)
            client.moderations.create(
                input="i want to kill them.",
                model="text-moderation-latest",
            )


@pytest.mark.parametrize("api_key_in_env", [True, False])
async def test_acreate_moderation(api_key_in_env, request_api_key, openai, openai_vcr, snapshot_tracer):
    with snapshot_context(
        token="tests.contrib.openai.test_openai.test_create_moderation",
        ignores=["meta.http.useragent", "meta.openai.api_type", "meta.openai.api_base"],
    ):
        with openai_vcr.use_cassette("moderation.yaml"):
            client = openai.AsyncOpenAI(api_key=request_api_key)
            await client.moderations.create(
                input="i want to kill them.",
                model="text-moderation-latest",
            )


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_misuse",
    ignores=[
        "meta.http.useragent",
        "meta.error.stack",
        "meta.error.type",
        "meta.openai.api_type",
        "meta.openai.api_base",
        "meta.error.message",
    ],
)
def test_misuse(openai, snapshot_tracer):
    with pytest.raises(TypeError):
        client = openai.OpenAI()
        client.completions.create(input="wrong arg")


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_span_finish_on_stream_error",
    ignores=[
        "meta.http.useragent",
        "meta.error.stack",
        "meta.openai.api_type",
        "meta.openai.api_base",
        "meta.error.type",
        "meta.error.message",
    ],
)
def test_span_finish_on_stream_error(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("completion_stream_wrong_api_key.yaml"):
        with pytest.raises((openai.APIConnectionError, openai.AuthenticationError)):
            client = openai.OpenAI(api_key="sk-wrong-api-key")
            client.completions.create(
                model="text-curie-001",
                prompt="how does openai tokenize prompts?",
                temperature=0.8,
                n=1,
                max_tokens=150,
                stream=True,
            )


@pytest.mark.snapshot
@pytest.mark.skipif(TIKTOKEN_AVAILABLE, reason="This test estimates token counts")
def test_completion_stream_est_tokens(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
            client = openai.OpenAI()
            resp = client.completions.create(model="ada", prompt="Hello world", stream=True, n=None)
            _ = [c for c in resp]


@pytest.mark.skipif(not TIKTOKEN_AVAILABLE, reason="This test computes token counts using tiktoken")
@pytest.mark.snapshot(token="tests.contrib.openai.test_openai.test_completion_stream")
def test_completion_stream(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
            client = openai.OpenAI()
            resp = client.completions.create(model="ada", prompt="Hello world", stream=True, n=None)
            _ = [c for c in resp]


@pytest.mark.skipif(not TIKTOKEN_AVAILABLE, reason="This test computes token counts using tiktoken")
@pytest.mark.snapshot(token="tests.contrib.openai.test_openai.test_completion_stream")
async def test_completion_async_stream(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
            client = openai.AsyncOpenAI()
            resp = await client.completions.create(model="ada", prompt="Hello world", stream=True, n=None)
            _ = [c async for c in resp]


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 6, 0) or not TIKTOKEN_AVAILABLE,
    reason="Streamed response context managers are only available v1.6.0+",
)
@pytest.mark.snapshot(token="tests.contrib.openai.test_openai.test_completion_stream")
def test_completion_stream_context_manager(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
            client = openai.OpenAI()
            with client.completions.create(model="ada", prompt="Hello world", stream=True, n=None) as resp:
                _ = [c for c in resp]


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
)
@pytest.mark.snapshot(token="tests.contrib.openai.test_openai.test_chat_completion_stream")
def test_chat_completion_stream(openai, openai_vcr, snapshot_tracer):
    """Assert that streamed token chunk extraction logic works automatically."""
    with openai_vcr.use_cassette("chat_completion_streamed_tokens.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8]
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "Who won the world series in 2020?"}],
                stream=True,
                user="ddtrace-test",
                n=None,
            )
            _ = [c for c in resp]


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
)
def test_chat_completion_stream_explicit_no_tokens(openai, openai_vcr, mock_tracer):
    """Assert that streamed token chunk extraction logic is avoided if explicitly set to False by the user."""
    with openai_vcr.use_cassette("chat_completion_streamed.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8]
            expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Who won the world series in 2020?"},
                ],
                stream=True,
                stream_options={"include_usage": False},
                user="ddtrace-test",
                n=None,
            )
            chunks = [c for c in resp]
            assert len(chunks) == 15
            completion = "".join([c.choices[0].delta.content for c in chunks if c.choices[0].delta.content is not None])
            assert completion == expected_completion

    span = mock_tracer.pop_traces()[0][0]
    assert span.get_metric("openai.response.usage.prompt_tokens") == 8
    assert span.get_metric("openai.response.usage.completion_tokens") is not None
    assert span.get_metric("openai.response.usage.total_tokens") is not None


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26, 0), reason="Streamed tokens available in 1.26.0+"
)
@pytest.mark.snapshot(token="tests.contrib.openai.test_openai.test_chat_completion_stream")
async def test_chat_completion_async_stream(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("chat_completion_streamed_tokens.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8]
            client = openai.AsyncOpenAI()
            resp = await client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Who won the world series in 2020?"},
                ],
                stream=True,
                n=None,
                user="ddtrace-test",
            )
            _ = [c async for c in resp]


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 26, 0),
    reason="Streamed response context managers are only available v1.6.0+, tokens available 1.26.0+",
)
@pytest.mark.snapshot(token="tests.contrib.openai.test_openai.test_chat_completion_stream")
async def test_chat_completion_async_stream_context_manager(openai, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("chat_completion_streamed_tokens.yaml"):
        with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
            mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8]
            client = openai.AsyncOpenAI()
            async with await client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "user", "content": "Who won the world series in 2020?"},
                ],
                stream=True,
                user="ddtrace-test",
                n=None,
            ) as resp:
                _ = [c async for c in resp]


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai_v1.test_integration_sync", ignores=["meta.http.useragent"], async_mode=False
)
def test_integration_sync(openai_api_key, ddtrace_run_python_code_in_subprocess):
    """OpenAI uses httpx for its synchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and httpx spans.

    FIXME: there _should_ be httpx spans generated for this test case. There aren't
           because the patching VCR does into httpx interferes with the tracing patching.
    """
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"OPENAI_API_KEY": openai_api_key, "PYTHONPATH": ":".join(pypath)})
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import openai
import ddtrace
from tests.contrib.openai.conftest import FilterOrg
from tests.contrib.openai.test_openai_v1 import get_openai_vcr
pin = ddtrace.trace.Pin.get_from(openai)
pin.tracer._configure(trace_processors=[FilterOrg()])
with get_openai_vcr(subdirectory_name="v1").use_cassette("completion.yaml"):
    client = openai.OpenAI()
    resp = client.completions.create(
        model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
    )
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai_v1.test_integration_async",
    ignores=["meta.http.useragent"],
    async_mode=False,
)
def test_integration_async(openai_api_key, ddtrace_run_python_code_in_subprocess):
    """OpenAI uses httpx for its asynchronous requests.

    Running in a subprocess with ddtrace-run should produce traces
    with both OpenAI and httpx spans.

    FIXME: there _should_ be httpx spans generated for this test case. There aren't
           because the patching VCR does into httpx interferes with the tracing patching.
    """
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"OPENAI_API_KEY": openai_api_key, "PYTHONPATH": ":".join(pypath)})
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
import openai
import ddtrace
from tests.contrib.openai.conftest import FilterOrg
from tests.contrib.openai.test_openai_v1 import get_openai_vcr
pin = ddtrace.trace.Pin.get_from(openai)
pin.tracer._configure(trace_processors=[FilterOrg()])
async def task():
    with get_openai_vcr(subdirectory_name="v1").use_cassette("completion.yaml"):
        client = openai.AsyncOpenAI()
        resp = await client.completions.create(
            model="ada", prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
        )

asyncio.run(task())
""",
        env=env,
    )
    assert status == 0, err
    assert out == b""
    assert err == b""


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(span_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]],
)
def test_completion_sample(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for completions endpoint"""
    num_completions = 200

    client = openai.OpenAI()
    for _ in range(num_completions):
        with openai_vcr.use_cassette("completion.yaml"):
            client.completions.create(model="ada", prompt="hello world")

    traces = mock_tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions, len(traces)
    for trace in traces:
        for span in trace:
            if span.get_tag("openai.response.choices.0.text"):
                sampled += 1
    if ddtrace.config.openai.span_prompt_completion_sample_rate == 0:
        assert sampled == 0
    elif ddtrace.config.openai.span_prompt_completion_sample_rate == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 30) < sampled < (rate + 30)


@pytest.mark.parametrize(
    "ddtrace_config_openai",
    [dict(span_prompt_completion_sample_rate=r) for r in [0, 0.25, 0.75, 1]],
)
def test_chat_completion_sample(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    """Test functionality for DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE for chat completions endpoint"""
    num_completions = 200

    client = openai.OpenAI()
    for _ in range(num_completions):
        with openai_vcr.use_cassette("chat_completion.yaml"):
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=multi_message_input,
                top_p=0.9,
                n=2,
                user="ddtrace-test",
            )

    traces = mock_tracer.pop_traces()
    sampled = 0
    assert len(traces) == num_completions
    for trace in traces:
        for span in trace:
            if span.get_tag("openai.response.choices.0.message.content"):
                sampled += 1
    if ddtrace.config.openai["span_prompt_completion_sample_rate"] == 0:
        assert sampled == 0
    elif ddtrace.config.openai["span_prompt_completion_sample_rate"] == 1:
        assert sampled == num_completions
    else:
        # this should be good enough for our purposes
        rate = ddtrace.config.openai["span_prompt_completion_sample_rate"] * num_completions
        assert (rate - 30) < sampled < (rate + 30)


@pytest.mark.parametrize("ddtrace_config_openai", [dict(truncation_threshold=t) for t in [0, 10, 10000]])
def test_completion_truncation(openai, openai_vcr, mock_tracer, ddtrace_config_openai):
    """Test functionality of DD_OPENAI_TRUNCATION_THRESHOLD for completions"""
    prompt = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10"

    with openai_vcr.use_cassette("completion_truncation.yaml"):
        client = openai.OpenAI()
        client.completions.create(model="ada", prompt=prompt)

    with openai_vcr.use_cassette("chat_completion_truncation.yaml"):
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Count from 1 to 100"},
            ],
        )
        assert resp.choices[0].message.content == (
            "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,"
            " 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55,"
            " 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81,"
            " 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100"
        )

    traces = mock_tracer.pop_traces()
    assert len(traces) == 2

    limit = ddtrace.config.openai["span_char_limit"]
    for trace in traces:
        for span in trace:
            if span.get_tag("openai.request.endpoint").endswith("/chat/completions"):
                prompt = span.get_tag("openai.request.messages.0.content")
                completion = span.get_tag("openai.response.choices.0.message.content")
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit
            else:
                prompt = span.get_tag("openai.request.prompt.0")
                completion = span.get_tag("openai.response.choices.0.text")
                # +3 for the ellipsis
                assert len(prompt) <= limit + 3
                assert len(completion) <= limit + 3
                if "..." in prompt:
                    assert len(prompt.replace("...", "")) == limit
                if "..." in completion:
                    assert len(completion.replace("...", "")) == limit


@pytest.mark.parametrize("ddtrace_config_openai", [dict(span_prompt_completion_sample_rate=0)])
def test_embedding_unsampled_prompt_completion(openai, openai_vcr, ddtrace_config_openai, mock_tracer):
    with openai_vcr.use_cassette("embedding.yaml"):
        client = openai.OpenAI()
        client.embeddings.create(input="hello world", model="text-embedding-ada-002")
    traces = mock_tracer.pop_traces()
    assert len(traces) == 1
    assert traces[0][0].get_tag("openai.request.input") is None


def test_est_tokens():
    """Oracle numbers are from https://platform.openai.com/tokenizer (GPT-3)."""
    assert _est_tokens("") == 0  # oracle: 1
    assert _est_tokens("hello") == 1  # oracle: 1
    assert _est_tokens("hello, world") == 3  # oracle: 3
    assert _est_tokens("hello world") == 2  # oracle: 2
    assert _est_tokens("Hello world, how are you?") == 6  # oracle: 7
    assert _est_tokens("    hello    ") == 3  # oracle: 8
    assert (
        _est_tokens(
            "The GPT family of models process text using tokens, which are common sequences of characters found in text. The models understand the statistical relationships between these tokens, and excel at producing the next token in a sequence of tokens."  # noqa E501
        )
        == 54
    )  # oracle: 44
    assert (
        _est_tokens(
            "You can use the tool below to understand how a piece of text would be tokenized by the API, and the total count of tokens in that piece of text."  # noqa: E501
        )
        == 33
    )  # oracle: 33
    assert (
        _est_tokens(
            "A helpful rule of thumb is that one token generally corresponds to ~4 characters of text for common "
            "English text. This translates to roughly ¾ of a word (so 100 tokens ~= 75 words). If you need a "
            "programmatic interface for tokenizing text, check out our tiktoken package for Python. For JavaScript, "
            "the gpt-3-encoder package for node.js works for most GPT-3 models."
        )
        == 83
    )  # oracle: 87

    # Expected to be a disparity since our assumption is based on english words
    assert (
        _est_tokens(
            """Lorem ipsum dolor sit amet, consectetur adipiscing elit. Donec hendrerit sapien eu erat imperdiet, in
 maximus elit malesuada. Pellentesque quis gravida purus. Nullam eu eros vitae dui placerat viverra quis a magna. Mauris
 vitae lorem quis neque pharetra congue. Praesent volutpat dui eget nibh auctor, sit amet elementum velit faucibus.
 Nullam ultricies dolor sit amet nisl molestie, a porta metus suscipit. Vivamus eget luctus mauris. Proin commodo
 elementum ex a pretium. Nam vitae ipsum sed dolor congue fermentum. Sed quis bibendum sapien, dictum venenatis urna.
 Morbi molestie lacinia iaculis. Proin lorem mauris, interdum eget lectus a, auctor volutpat nisl. Suspendisse ac
 tincidunt sapien. Cras congue ipsum sit amet congue ullamcorper. Proin hendrerit at erat vulputate consequat."""
        )
        == 175
    )  # oracle 281

    assert (
        _est_tokens(
            "I want you to act as a linux terminal. I will type commands and you will reply with what the terminal should show. I want you to only reply with the terminal output inside one unique code block, and nothing else. do not write explanations. do not type commands unless I instruct you to do so. When I need to tell you something in English, I will do so by putting text inside curly brackets {like this}. My first command is pwd"  # noqa: E501
        )
        == 97
    )  # oracle: 92


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 60), reason="latest openai versions use modified azure requests"
)
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_completion",
    ignores=["meta.http.useragent", "meta.openai.api_base", "meta.openai.api_type", "meta.openai.api_version"],
)
def test_azure_openai_completion(openai, azure_openai_config, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_completion.yaml"):
        azure_client = openai.AzureOpenAI(
            api_version=azure_openai_config["api_version"],
            azure_endpoint=azure_openai_config["azure_endpoint"],
            azure_deployment=azure_openai_config["azure_deployment"],
            api_key=azure_openai_config["api_key"],
        )
        azure_client.completions.create(
            model="gpt-35-turbo",
            prompt="why do some languages have words that can't directly be translated to other languages?",
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 60), reason="latest openai versions use modified azure requests"
)
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_completion",
    ignores=[
        "meta.http.useragent",
        "meta.openai.api_base",
        "meta.openai.api_type",
        "meta.openai.api_version",
        "meta.openai.response.id",
        "metrics.openai.response.created",
    ],
)
async def test_azure_openai_acompletion(openai, azure_openai_config, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_completion.yaml"):
        azure_client = openai.AsyncAzureOpenAI(
            api_version=azure_openai_config["api_version"],
            azure_endpoint=azure_openai_config["azure_endpoint"],
            azure_deployment=azure_openai_config["azure_deployment"],
            api_key=azure_openai_config["api_key"],
        )
        await azure_client.completions.create(
            model="gpt-35-turbo",
            prompt="why do some languages have words that can't directly be translated to other languages?",
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 60), reason="latest openai versions use modified azure requests"
)
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_chat_completion",
    ignores=["meta.http.useragent", "meta.openai.api_base", "meta.openai.api_type", "meta.openai.api_version"],
)
def test_azure_openai_chat_completion(openai, azure_openai_config, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_chat_completion.yaml"):
        azure_client = openai.AzureOpenAI(
            api_version=azure_openai_config["api_version"],
            azure_endpoint=azure_openai_config["azure_endpoint"],
            azure_deployment=azure_openai_config["azure_deployment"],
            api_key=azure_openai_config["api_key"],
        )
        azure_client.chat.completions.create(
            model="gpt-35-turbo",
            messages=[{"role": "user", "content": "What's the weather like in NYC right now?"}],
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 60), reason="latest openai versions use modified azure requests"
)
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_chat_completion",
    ignores=["meta.http.useragent", "meta.openai.api_base", "meta.openai.api_type", "meta.openai.api_version"],
)
async def test_azure_openai_chat_acompletion(openai, azure_openai_config, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_chat_completion.yaml"):
        azure_client = openai.AsyncAzureOpenAI(
            api_version=azure_openai_config["api_version"],
            azure_endpoint=azure_openai_config["azure_endpoint"],
            azure_deployment=azure_openai_config["azure_deployment"],
            api_key=azure_openai_config["api_key"],
        )
        await azure_client.chat.completions.create(
            model="gpt-35-turbo",
            messages=[{"role": "user", "content": "What's the weather like in NYC right now?"}],
            temperature=0,
            n=1,
            max_tokens=20,
            user="ddtrace-test",
        )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 60), reason="latest openai versions use modified azure requests"
)
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_embedding",
    ignores=["meta.http.useragent", "meta.openai.api_base", "meta.openai.api_type", "meta.openai.api_version"],
)
def test_azure_openai_embedding(openai, azure_openai_config, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_embedding.yaml"):
        azure_client = openai.AzureOpenAI(
            api_version=azure_openai_config["api_version"],
            azure_endpoint=azure_openai_config["azure_endpoint"],
            azure_deployment=azure_openai_config["azure_deployment"],
            api_key=azure_openai_config["api_key"],
        )
        azure_client.embeddings.create(
            model="text-embedding-ada-002",
            input="Hello world",
            user="ddtrace-test",
        )


@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 60), reason="latest openai versions use modified azure requests"
)
@pytest.mark.snapshot(
    token="tests.contrib.openai.test_openai.test_azure_openai_embedding",
    ignores=["meta.http.useragent", "meta.openai.api_base", "meta.openai.api_type", "meta.openai.api_version"],
)
async def test_azure_openai_aembedding(openai, azure_openai_config, openai_vcr, snapshot_tracer):
    with openai_vcr.use_cassette("azure_embedding.yaml"):
        azure_client = openai.AsyncAzureOpenAI(
            api_version=azure_openai_config["api_version"],
            azure_endpoint=azure_openai_config["azure_endpoint"],
            azure_deployment=azure_openai_config["azure_deployment"],
            api_key=azure_openai_config["api_key"],
        )
        await azure_client.embeddings.create(
            model="text-embedding-ada-002",
            input="Hello world",
            user="ddtrace-test",
        )


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
@pytest.mark.parametrize("service_name", [None, "mysvc"])
def test_integration_service_name(openai_api_key, ddtrace_run_python_code_in_subprocess, schema_version, service_name):
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"OPENAI_API_KEY": openai_api_key, "PYTHONPATH": ":".join(pypath)})
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if service_name:
        env["DD_SERVICE"] = service_name
    with snapshot_context(
        token="tests.contrib.openai.test_openai_v1.test_integration_service_name[%s-%s]"
        % (service_name, schema_version),
        ignores=["meta.http.useragent", "meta.openai.api_base", "meta.openai.api_type"],
        async_mode=False,
    ):
        out, err, status, pid = ddtrace_run_python_code_in_subprocess(
            """
import openai
import ddtrace
from tests.contrib.openai.conftest import FilterOrg
from tests.contrib.openai.test_openai_v1 import get_openai_vcr
pin = ddtrace.trace.Pin.get_from(openai)
pin.tracer._configure(trace_processors=[FilterOrg()])
with get_openai_vcr(subdirectory_name="v1").use_cassette("completion.yaml"):
    client = openai.OpenAI()
    resp = client.completions.create(model="ada", prompt="hello world")
    """,
            env=env,
        )
        assert status == 0, err
        assert out == b""
        assert err == b""


async def test_openai_asyncio_cancellation(openai):
    import asyncio

    import httpx

    class DelayedTransport(httpx.AsyncBaseTransport):
        def __init__(self, delay: float):
            self.delay = delay
            self._transport = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            # Introduce a delay before making the actual request
            await asyncio.sleep(self.delay)
            return await self._transport.handle_async_request(request)

    client = openai.AsyncOpenAI(http_client=httpx.AsyncClient(transport=DelayedTransport(delay=10)))
    asyncio_timeout = False

    try:
        await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": "Write a Python program that writes a Python program for a given task.",
                    },
                ],
                user="ddtrace-test",
            ),
            timeout=1,
        )
    except asyncio.TimeoutError:
        asyncio_timeout = True
    except Exception as e:
        assert False, f"Unexpected exception: {e}"

    assert asyncio_timeout, "Expected asyncio.TimeoutError"
