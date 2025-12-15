import json

import mock
import openai as openai_module
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.utils import _est_tokens
from ddtrace.llmobs._utils import safe_json
from tests.contrib.openai.utils import assert_prompt_tracking
from tests.contrib.openai.utils import chat_completion_custom_functions
from tests.contrib.openai.utils import chat_completion_input_description
from tests.contrib.openai.utils import get_openai_vcr
from tests.contrib.openai.utils import mock_openai_chat_completions_response
from tests.contrib.openai.utils import mock_openai_completions_response
from tests.contrib.openai.utils import mock_response_mcp_tool_call
from tests.contrib.openai.utils import multi_message_input
from tests.contrib.openai.utils import response_tool_function
from tests.contrib.openai.utils import response_tool_function_expected_output
from tests.contrib.openai.utils import response_tool_function_expected_output_streamed
from tests.contrib.openai.utils import tool_call_expected_output
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


EXPECTED_TOOL_DEFINITIONS = [
    {
        "name": "extract_student_info",
        "description": "Get the student information from the body of the input text",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the person"},
                "major": {"type": "string", "description": "Major subject."},
                "school": {"type": "string", "description": "The university name."},
                "grades": {"type": "integer", "description": "GPA of the student."},
                "clubs": {
                    "type": "array",
                    "description": "School clubs for extracurricular activities. ",
                    "items": {"type": "string", "description": "Name of School Club"},
                },
            },
        },
    }
]


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        dict(
            _llmobs_enabled=True,
            _llmobs_sample_rate=1.0,
            _llmobs_ml_app="<ml-app-name>",
            _llmobs_instrumented_proxy_urls="http://localhost:4000",
        )
    ],
)
class TestLLMObsOpenaiV1:
    @mock.patch("openai._base_client.SyncAPIClient.post")
    def test_completion_proxy(
        self, mock_completions_post, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        # mock out the completions response
        mock_completions_post.return_value = mock_openai_completions_response
        model = "gpt-3.5-turbo"
        client = openai.OpenAI(base_url="http://localhost:4000")
        client.completions.create(
            model=model,
            prompt="Hello world",
            temperature=0.8,
            n=2,
            stop=".",
            max_tokens=10,
            user="ddtrace-test",
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                "workflow",
                input_value=safe_json([{"content": "Hello world"}], ensure_ascii=False),
                output_value=safe_json(
                    [
                        {"content": "Hello! How can I assist you today?"},
                        {"content": "Hello! How can I assist you today?"},
                    ],
                    ensure_ascii=False,
                ),
                metadata={
                    "temperature": 0.8,
                    "n": 2,
                    "stop": ".",
                    "max_tokens": 10,
                    "user": "ddtrace-test",
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

        # span created from request with non-proxy URL should result in an LLM span
        client = openai.OpenAI(base_url="http://localhost:8000")
        client.completions.create(
            model=model,
            prompt="Hello world",
            temperature=0.8,
            n=2,
            stop=".",
            max_tokens=10,
            user="ddtrace-test",
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span"]["kind"] == "llm"

    def test_completion(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        with get_openai_vcr(subdirectory_name="v1").use_cassette("completion.yaml"):
            model = "ada"
            client = openai.OpenAI()
            client.completions.create(
                model=model,
                prompt="Hello world",
                temperature=0.8,
                n=2,
                stop=".",
                max_tokens=10,
                user="ddtrace-test",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=model,
                model_provider="openai",
                input_messages=[{"content": "Hello world"}],
                output_messages=[{"content": ", relax!” I said to my laptop"}, {"content": " (1"}],
                metadata={"temperature": 0.8, "max_tokens": 10, "n": 2, "stop": ".", "user": "ddtrace-test"},
                token_metrics={"input_tokens": 2, "output_tokens": 12, "total_tokens": 14},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    @mock.patch("openai._base_client.SyncAPIClient.post")
    def test_completion_azure_proxy(
        self, mock_completions_post, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        prompt = "Hello world"
        mock_completions_post.return_value = mock_openai_completions_response
        azure_client = openai.AzureOpenAI(
            base_url="http://localhost:4000",
            api_key=azure_openai_config["api_key"],
            api_version=azure_openai_config["api_version"],
        )
        azure_client.completions.create(
            model="gpt-3.5-turbo", prompt=prompt, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                "workflow",
                input_value=safe_json([{"content": "Hello world"}], ensure_ascii=False),
                output_value=safe_json(
                    [
                        {"content": "Hello! How can I assist you today?"},
                        {"content": "Hello! How can I assist you today?"},
                    ],
                    ensure_ascii=False,
                ),
                metadata={
                    "temperature": 0,
                    "n": 1,
                    "max_tokens": 20,
                    "user": "ddtrace-test",
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

        # span created from request with non-proxy URL should result in an LLM span
        azure_client = openai.AzureOpenAI(
            base_url="http://localhost:8000",
            api_key=azure_openai_config["api_key"],
            api_version=azure_openai_config["api_version"],
        )
        azure_client.completions.create(
            model="gpt-3.5-turbo", prompt=prompt, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span"]["kind"] == "llm"

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    def test_completion_azure(
        self, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        prompt = "why do some languages have words that can't directly be translated to other languages?"
        expected_output = (
            '". The answer is that languages are not just a collection of words, but also a collection of cultural'  # noqa: E501
        )
        with get_openai_vcr(subdirectory_name="v1").use_cassette("azure_completion.yaml"):
            azure_client = openai.AzureOpenAI(
                api_version=azure_openai_config["api_version"],
                azure_endpoint=azure_openai_config["azure_endpoint"],
                azure_deployment=azure_openai_config["azure_deployment"],
                api_key=azure_openai_config["api_key"],
            )
            resp = azure_client.completions.create(
                model="gpt-35-turbo", prompt=prompt, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="azure_openai",
                input_messages=[{"content": prompt}],
                output_messages=[{"content": expected_output}],
                metadata={"temperature": 0, "max_tokens": 20, "n": 1, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 16, "output_tokens": 20, "total_tokens": 36},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    async def test_completion_azure_async(
        self, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        prompt = "why do some languages have words that can't directly be translated to other languages?"
        expected_output = (
            '". The answer is that languages are not just a collection of words, but also a collection of cultural'  # noqa: E501
        )
        with get_openai_vcr(subdirectory_name="v1").use_cassette("azure_completion.yaml"):
            azure_client = openai.AsyncAzureOpenAI(
                api_version=azure_openai_config["api_version"],
                azure_endpoint=azure_openai_config["azure_endpoint"],
                azure_deployment=azure_openai_config["azure_deployment"],
                api_key=azure_openai_config["api_key"],
            )
            resp = await azure_client.completions.create(
                model="gpt-35-turbo", prompt=prompt, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="azure_openai",
                input_messages=[{"content": prompt}],
                output_messages=[{"content": expected_output}],
                metadata={"temperature": 0, "max_tokens": 20, "n": 1, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 16, "output_tokens": 20, "total_tokens": 36},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("completion_streamed.yaml"):
            with mock.patch("ddtrace.llmobs._integrations.utils._est_tokens") as mock_est:
                mock_est.return_value = 2
                model = "ada"
                expected_completion = '! ... A page layouts page drawer? ... Interesting. The "Tools" is'
                client = openai.OpenAI()
                resp = client.completions.create(model=model, prompt="Hello world", stream=True)
                for _ in resp:
                    pass
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=model,
                model_provider="openai",
                input_messages=[{"content": "Hello world"}],
                output_messages=[{"content": expected_completion}],
                metadata={"stream": True},
                token_metrics={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            ),
        )

    @mock.patch("openai._base_client.SyncAPIClient.post")
    def test_chat_completion_proxy(
        self, mock_completions_post, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        mock_completions_post.return_value = mock_openai_chat_completions_response
        model = "gpt-3.5-turbo"
        input_messages = multi_message_input
        client = openai.OpenAI(base_url="http://localhost:4000")
        client.chat.completions.create(model=model, messages=input_messages, top_p=0.9, n=2, user="ddtrace-test")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                "workflow",
                input_value=safe_json(input_messages, ensure_ascii=False),
                output_value=safe_json(
                    [
                        {
                            "content": "The 2020 World Series was played at Globe Life Field in Arlington, Texas.",
                            "role": "assistant",
                        },
                        {
                            "content": "The 2020 World Series was played at Globe Life Field in Arlington, Texas.",
                            "role": "assistant",
                        },
                    ],
                    ensure_ascii=False,
                ),
                metadata={
                    "top_p": 0.9,
                    "n": 2,
                    "user": "ddtrace-test",
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

        # span created from request with non-proxy URL should result in an LLM span
        client = openai.OpenAI(base_url="http://localhost:8000")
        client.chat.completions.create(model=model, messages=input_messages, top_p=0.9, n=2, user="ddtrace-test")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span"]["kind"] == "llm"

    def test_chat_completion(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion.yaml"):
            model = "gpt-3.5-turbo"
            input_messages = multi_message_input
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model=model, messages=input_messages, top_p=0.9, n=2, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"role": "assistant", "content": choice.message.content} for choice in resp.choices],
                metadata={"top_p": 0.9, "n": 2, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 57, "output_tokens": 34, "total_tokens": 91},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    @mock.patch("openai._base_client.SyncAPIClient.post")
    def test_chat_completion_azure_proxy(
        self, mock_completions_post, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        input_messages = [
            {"content": "Where did the Los Angeles Dodgers play to win the world series in 2020?", "role": "user"}
        ]
        mock_completions_post.return_value = mock_openai_chat_completions_response
        azure_client = openai.AzureOpenAI(
            base_url="http://localhost:4000",
            api_key=azure_openai_config["api_key"],
            api_version=azure_openai_config["api_version"],
        )
        azure_client.chat.completions.create(
            model="gpt-3.5-turbo", messages=input_messages, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            "workflow",
            input_value=safe_json(input_messages, ensure_ascii=False),
            output_value=safe_json(
                [
                    {
                        "content": "The 2020 World Series was played at Globe Life Field in Arlington, Texas.",
                        "role": "assistant",
                    },
                    {
                        "content": "The 2020 World Series was played at Globe Life Field in Arlington, Texas.",
                        "role": "assistant",
                    },
                ],
                ensure_ascii=False,
            ),
            metadata={
                "temperature": 0,
                "n": 1,
                "max_tokens": 20,
                "user": "ddtrace-test",
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_event)

        # span created from request with non-proxy URL should result in an LLM span
        azure_client = openai.AzureOpenAI(
            base_url="http://localhost:8000",
            api_key=azure_openai_config["api_key"],
            api_version=azure_openai_config["api_version"],
        )
        azure_client.chat.completions.create(
            model="gpt-3.5-turbo", messages=input_messages, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span"]["kind"] == "llm"

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    def test_chat_completion_azure(
        self, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        input_messages = [{"role": "user", "content": "What's the weather like in NYC right now?"}]
        expected_output = "I'm sorry, as an AI language model, I do not have real-time information. Please check"
        with get_openai_vcr(subdirectory_name="v1").use_cassette("azure_chat_completion.yaml"):
            azure_client = openai.AzureOpenAI(
                api_version=azure_openai_config["api_version"],
                azure_endpoint=azure_openai_config["azure_endpoint"],
                azure_deployment=azure_openai_config["azure_deployment"],
                api_key=azure_openai_config["api_key"],
            )
            resp = azure_client.chat.completions.create(
                model="gpt-35-turbo", messages=input_messages, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="azure_openai",
                input_messages=input_messages,
                output_messages=[{"role": "assistant", "content": expected_output}],
                metadata={"temperature": 0, "max_tokens": 20, "n": 1, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 18, "output_tokens": 20, "total_tokens": 38},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    def test_chat_completion_azure_streamed(
        self, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        input_messages = [{"role": "user", "content": "What's the weather like in NYC right now?"}]
        expected_output = (
            "I'm unable to provide real-time weather updates. To find the current weather in New York City, "
            "I recommend checking a reliable weather website or app for the most accurate and up-to-date information."
        )
        with get_openai_vcr(subdirectory_name="v1").use_cassette("azure_chat_completion_streamed.yaml"):
            azure_client = openai.AzureOpenAI(
                api_version=azure_openai_config["api_version"],
                azure_endpoint=azure_openai_config["azure_endpoint"],
                azure_deployment=azure_openai_config["azure_deployment"],
                api_key=azure_openai_config["api_key"],
            )
            resp = azure_client.chat.completions.create(
                model="gpt-4o-mini",
                stream=True,
                messages=input_messages,
                temperature=0,
                n=1,
                max_tokens=300,
                user="ddtrace-test",
            )
            for chunk in resp:
                pass
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1

        expected_metadata = {
            "stream": True,
            "temperature": 0,
            "n": 1,
            "max_tokens": 300,
            "user": "ddtrace-test",
        }
        if parse_version(openai_module.version.VERSION) >= (1, 26):
            expected_metadata["stream_options"] = {"include_usage": True}

        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gpt-4o-mini-2024-07-18",
                model_provider="azure_openai",
                input_messages=input_messages,
                # note: investigate why role is empty; in the streamed chunks there is no role returned.
                output_messages=[{"content": expected_output, "role": ""}],
                metadata=expected_metadata,
                token_metrics={"input_tokens": 9, "output_tokens": 45, "total_tokens": 54},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    async def test_chat_completion_azure_async(
        self, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        input_messages = [{"role": "user", "content": "What's the weather like in NYC right now?"}]
        expected_output = "I'm sorry, as an AI language model, I do not have real-time information. Please check"
        with get_openai_vcr(subdirectory_name="v1").use_cassette("azure_chat_completion.yaml"):
            azure_client = openai.AsyncAzureOpenAI(
                api_version=azure_openai_config["api_version"],
                azure_endpoint=azure_openai_config["azure_endpoint"],
                azure_deployment=azure_openai_config["azure_deployment"],
                api_key=azure_openai_config["api_key"],
            )
            resp = await azure_client.chat.completions.create(
                model="gpt-35-turbo", messages=input_messages, temperature=0, n=1, max_tokens=20, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="azure_openai",
                input_messages=input_messages,
                output_messages=[{"role": "assistant", "content": expected_output}],
                metadata={"temperature": 0, "max_tokens": 20, "n": 1, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 18, "output_tokens": 20, "total_tokens": 38},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
    )
    def test_chat_completion_stream_explicit_no_tokens(
        self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """

        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_streamed.yaml"):
            with mock.patch("ddtrace.llmobs._integrations.utils._est_tokens") as mock_est:
                mock_est.return_value = 8
                model = "gpt-3.5-turbo"
                resp_model = model
                input_messages = [{"role": "user", "content": "Who won the world series in 2020?"}]
                expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
                client = openai.OpenAI()
                resp = client.chat.completions.create(
                    model=model,
                    messages=input_messages,
                    stream=True,
                    user="ddtrace-test",
                    stream_options={"include_usage": False},
                )
                for chunk in resp:
                    resp_model = chunk.model
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"content": expected_completion, "role": "assistant"}],
                metadata={"stream": True, "stream_options": {"include_usage": False}, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 8, "output_tokens": 8, "total_tokens": 16},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 26, 0), reason="Streamed tokens available in 1.26.0+"
    )
    def test_chat_completion_stream_tokens(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Assert that streamed token chunk extraction logic works when options are not explicitly passed from user."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_streamed_tokens.yaml"):
            model = "gpt-3.5-turbo"
            resp_model = model
            input_messages = [{"role": "user", "content": "Who won the world series in 2020?"}]
            expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
            client = openai.OpenAI()
            resp = client.chat.completions.create(model=model, messages=input_messages, stream=True)
            for chunk in resp:
                resp_model = chunk.model
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"content": expected_completion, "role": "assistant"}],
                metadata={"stream": True, "stream_options": {"include_usage": True}},
                token_metrics={"input_tokens": 17, "output_tokens": 19, "total_tokens": 36},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 40, 0),
        reason="`client.beta.chat.completions.stream` available in 1.40.0+",
    )
    def test_chat_completion_stream_tokens_beta(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Assert that streamed token chunk extraction logic works when options are not explicitly passed from user."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_streamed_tokens.yaml"):
            model = "gpt-3.5-turbo"
            resp_model = model
            input_messages = [{"role": "user", "content": "Who won the world series in 2020?"}]
            expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
            client = openai.OpenAI()
            with client.beta.chat.completions.stream(model=model, messages=input_messages) as stream:
                for chunk in stream:
                    if hasattr(chunk, "chunk") and hasattr(chunk.chunk, "model"):
                        resp_model = chunk.chunk.model
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        llmobs_span_event = mock_llmobs_writer.enqueue.call_args_list[0][0][0]
        assert llmobs_span_event["meta"]["metadata"]["stream_options"]["include_usage"] is True
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"content": expected_completion, "role": "assistant"}],
                metadata=mock.ANY,
                token_metrics={"input_tokens": 17, "output_tokens": 19, "total_tokens": 36},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_chat_completion_function_call(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that function call chat completion calls are recorded as LLMObs events correctly."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_function_call.yaml"):
            model = "gpt-3.5-turbo"
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": chat_completion_input_description}],
                functions=chat_completion_custom_functions,
                function_call="auto",
                user="ddtrace-test",
            )
        expected_output = {
            "content": "",
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "extract_student_info",
                    "arguments": {
                        "name": "David Nguyen",
                        "major": "computer science",
                        "school": "Stanford University",
                        "grades": 3.8,
                        "clubs": ["Chess Club", "South Asian Student Association"],
                    },
                }
            ],
        }
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[expected_output],
                metadata={"function_call": "auto", "user": "ddtrace-test"},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tool_definitions=EXPECTED_TOOL_DEFINITIONS,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 1), reason="Tool calls available after v1.1.0"
    )
    def test_chat_completion_tool_call(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that tool call chat completion calls are recorded as LLMObs events correctly."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_tool_call.yaml"):
            model = "gpt-3.5-turbo"
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                tools=chat_completion_custom_functions,
                model=model,
                messages=[{"role": "user", "content": chat_completion_input_description}],
                user="ddtrace-test",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[tool_call_expected_output],
                metadata={"user": "ddtrace-test"},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tool_definitions=EXPECTED_TOOL_DEFINITIONS,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 1), reason="Tool calls available after v1.1.0"
    )
    def test_chat_completion_tool_call_with_follow_up(
        self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        """Test a conversation flow where a tool call response is used in a follow-up message."""
        model = "gpt-3.5-turbo"
        messages = [{"role": "user", "content": chat_completion_input_description}]
        client = openai.OpenAI()
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_tool_call.yaml"):
            first_resp = client.chat.completions.create(
                tools=chat_completion_custom_functions,
                model=model,
                messages=messages,
                user="ddtrace-test",
            )
        tool_call_id = first_resp.choices[0].message.tool_calls[0].id
        tool_name = first_resp.choices[0].message.tool_calls[0].function.name
        tool_arguments_str = first_resp.choices[0].message.tool_calls[0].function.arguments

        tool_result = {"status": "success", "gpa_verified": True}
        messages += [
            first_resp.choices[0].message,
            {
                "role": "tool",
                "tool_call_id": first_resp.choices[0].message.tool_calls[0].id,
                "content": json.dumps(tool_result),
            },
            {"role": "user", "content": "Can you summarize the student's academic performance?"},
        ]
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_tool_call_follow_up.yaml"):
            second_resp = client.chat.completions.create(
                model=model,
                messages=messages,
                user="ddtrace-test",
            )

        spans = mock_tracer.pop_traces()
        span1, span2 = spans[0][0], spans[1][0]
        assert mock_llmobs_writer.enqueue.call_count == 2

        mock_llmobs_writer.enqueue.assert_has_calls(
            [
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span1,
                        model_name=first_resp.model,
                        model_provider="openai",
                        input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                        output_messages=[
                            {
                                "content": "",
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": tool_name,
                                        "arguments": json.loads(tool_arguments_str),
                                        "tool_id": tool_call_id,
                                        "type": "function",
                                    }
                                ],
                            }
                        ],
                        metadata={"user": "ddtrace-test"},
                        token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                        tool_definitions=[
                            {
                                "name": "extract_student_info",
                                "description": "Get the student information from the body of the input text",
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "Name of the person"},
                                        "major": {"type": "string", "description": "Major subject."},
                                        "school": {"type": "string", "description": "The university name."},
                                        "grades": {"type": "integer", "description": "GPA of the student."},
                                        "clubs": {
                                            "type": "array",
                                            "description": "School clubs for extracurricular activities. ",
                                            "items": {"type": "string", "description": "Name of School Club"},
                                        },
                                    },
                                },
                            }
                        ],
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span2,
                        model_name=second_resp.model,
                        model_provider="openai",
                        input_messages=[
                            {"content": chat_completion_input_description, "role": "user"},
                            {
                                "content": "",
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "name": tool_name,
                                        "arguments": json.loads(tool_arguments_str),
                                        "tool_id": tool_call_id,
                                        "type": "function",
                                    }
                                ],
                            },
                            {
                                "content": "",
                                "role": "tool",
                                "tool_results": [
                                    {
                                        "name": "",
                                        "result": json.dumps(tool_result),
                                        "tool_id": tool_call_id,
                                        "type": "tool_result",
                                    }
                                ],
                            },
                            {"content": "Can you summarize the student's academic performance?", "role": "user"},
                        ],
                        output_messages=[
                            {
                                "content": (
                                    "David Nguyen is a sophomore majoring in computer science at Stanford "
                                    "University with a GPA of 3.8. His academic performance is impressive "
                                    "and he is excelling in his studies."
                                ),
                                "role": "assistant",
                            }
                        ],
                        metadata={"user": "ddtrace-test"},
                        token_metrics={
                            "input_tokens": 143,
                            "output_tokens": 36,
                            "total_tokens": 179,
                            "cache_read_input_tokens": 0,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
            ]
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66),
        reason="Responses API with custom tools available after v1.66.0",
    )
    def test_response_custom_tool_call(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that custom tool calls in responses API are recorded as LLMObs events correctly."""
        grammar = """
        start: expr
        expr: term (SP ADD SP term)* -> add
        | term
        term: factor (SP MUL SP factor)* -> mul
        | factor
        factor: INT
        SP: " "
        ADD: "+"
        MUL: "*"
        %import common.INT
        """
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_custom_tool_call.yaml"):
            client = openai.OpenAI()
            resp = client.responses.create(
                model="gpt-5",
                input="Use the math_exp tool to add four plus four.",
                tools=[
                    {
                        "type": "custom",
                        "name": "math_exp",
                        "description": "Creates valid mathematical expressions",
                        "format": {
                            "type": "grammar",
                            "syntax": "lark",
                            "definition": grammar,
                        },
                    }
                ],
            )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"role": "user", "content": "Use the math_exp tool to add four plus four."}],
                output_messages=[
                    {
                        "role": "reasoning",
                        "content": mock.ANY,  # reasoning content unstable
                    },
                    {
                        "tool_calls": [
                            {
                                "tool_id": "call_H8YsBUDPYjNHSq9fWOzwetxr",
                                "arguments": {"value": "4 + 4"},
                                "name": "math_exp",
                                "type": "custom_tool_call",
                            }
                        ],
                        "role": "assistant",
                    },
                ],
                token_metrics={
                    "input_tokens": 159,
                    "output_tokens": 275,
                    "total_tokens": 434,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 256,
                },
                tool_definitions=[
                    {
                        "name": "math_exp",
                        "description": "Creates valid mathematical expressions",
                        "schema": {
                            "type": "grammar",
                            "syntax": "lark",
                            "definition": grammar,
                        },
                    }
                ],
                metadata={
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}, "verbosity": "medium"},
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 1),
        reason="Tool calls available after v1.1.0",
    )
    def test_chat_completion_custom_tool_call(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that custom tool calls in chat completions API are recorded as LLMObs events correctly."""
        grammar = """
start: expr
expr: term (SP ADD SP term)* -> add
| term
term: factor (SP MUL SP factor)* -> mul
| factor
factor: INT
SP: " "
ADD: "+"
MUL: "*"
%import common.INT
"""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_custom_tool_call.yaml"):
            model = "gpt-5"
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": "Use the math_exp tool to add four plus four."},
                ],
                tools=[
                    {
                        "type": "custom",
                        "custom": {
                            "name": "math_exp",
                            "description": "Creates valid mathematical expressions",
                            "format": {"type": "grammar", "grammar": {"syntax": "lark", "definition": grammar}},
                        },
                    },
                ],
            )

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": "Use the math_exp tool to add four plus four.", "role": "user"}],
                output_messages=[
                    {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "name": "math_exp",
                                "arguments": {"value": "4 + 4"},
                                "tool_id": mock.ANY,
                                "type": "custom",
                            }
                        ],
                    }
                ],
                token_metrics={
                    "input_tokens": 241,
                    "output_tokens": 214,
                    "total_tokens": 455,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 192,
                },
                tool_definitions=[
                    {
                        "name": "math_exp",
                        "description": "Creates valid mathematical expressions",
                        "schema": {"type": "grammar", "grammar": {"syntax": "lark", "definition": grammar}},
                    }
                ],
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 26, 0), reason="Streamed tokens available in 1.26.0+"
    )
    def test_chat_completion_tool_call_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that tool call chat completion calls are recorded as LLMObs events correctly."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_tool_call_streamed.yaml"):
            model = "gpt-3.5-turbo"
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                tools=chat_completion_custom_functions,
                model=model,
                messages=[{"role": "user", "content": chat_completion_input_description}],
                user="ddtrace-test",
                stream=True,
            )
            for chunk in resp:
                resp_model = chunk.model
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[tool_call_expected_output],
                metadata={"user": "ddtrace-test", "stream": True, "stream_options": {"include_usage": True}},
                token_metrics={"input_tokens": 166, "output_tokens": 43, "total_tokens": 209},
                tool_definitions=EXPECTED_TOOL_DEFINITIONS,
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_completion_error(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure erroneous llmobs records are emitted for completion endpoints when configured."""
        with pytest.raises(Exception):
            with get_openai_vcr(subdirectory_name="v1").use_cassette("completion_error.yaml"):
                model = "babbage-002"
                client = openai.OpenAI()
                client.completions.create(
                    model=model,
                    prompt="Hello world",
                    temperature=0.8,
                    n=2,
                    stop=".",
                    max_tokens=10,
                    user="ddtrace-test",
                )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=model,
                model_provider="openai",
                input_messages=[{"content": "Hello world"}],
                output_messages=[{"content": ""}],
                metadata={"temperature": 0.8, "max_tokens": 10, "n": 2, "stop": ".", "user": "ddtrace-test"},
                token_metrics={},
                error="openai.AuthenticationError",
                error_message="Error code: 401 - {'error': {'message': 'Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_chat_completion_error(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure erroneous llmobs records are emitted for chat completion endpoints when configured."""
        with pytest.raises(Exception):
            with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_error.yaml"):
                model = "gpt-3.5-turbo"
                client = openai.OpenAI()
                input_messages = multi_message_input
                client.chat.completions.create(
                    model=model, messages=input_messages, top_p=0.9, n=2, user="ddtrace-test"
                )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"content": ""}],
                metadata={"n": 2, "top_p": 0.9, "user": "ddtrace-test"},
                token_metrics={},
                error="openai.AuthenticationError",
                error_message="Error code: 401 - {'error': {'message': 'Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_chat_completion_prompt_caching(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that prompt caching metrics are properly captured"""
        model = "gpt-4o"
        client = openai.OpenAI()
        base_messages = [{"role": "system", "content": "You are an expert software engineer " * 200}]
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_prompt_caching_cache_write.yaml"):
            resp1 = client.chat.completions.create(
                model=model,
                messages=base_messages + [{"role": "user", "content": "What are the best practices for API design?"}],
                max_tokens=100,
                temperature=0.1,
            )
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_prompt_caching_cache_read.yaml"):
            resp2 = client.chat.completions.create(
                model=model,
                messages=base_messages + [{"role": "user", "content": "How should I structure my database schema?"}],
                max_tokens=100,
                temperature=0.1,
            )
        spans = mock_tracer.pop_traces()
        span1, span2 = spans[0][0], spans[1][0]
        assert mock_llmobs_writer.enqueue.call_count == 2

        mock_llmobs_writer.enqueue.assert_has_calls(
            [
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span1,
                        model_name=resp1.model,
                        model_provider="openai",
                        input_messages=base_messages
                        + [{"role": "user", "content": "What are the best practices for API design?"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={"max_tokens": 100, "temperature": 0.1},
                        token_metrics={
                            "input_tokens": 1221,
                            "output_tokens": 100,
                            "total_tokens": 1321,
                            "cache_read_input_tokens": 0,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span2,
                        model_name=resp2.model,
                        model_provider="openai",
                        input_messages=base_messages
                        + [{"role": "user", "content": "How should I structure my database schema?"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={"max_tokens": 100, "temperature": 0.1},
                        token_metrics={
                            "input_tokens": 1220,
                            "output_tokens": 100,
                            "total_tokens": 1320,
                            "cache_read_input_tokens": 1152,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
            ]
        )

    def test_embedding_string(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("embedding.yaml"):
            client = openai.OpenAI()
            resp = client.embeddings.create(input="hello world", model="text-embedding-ada-002")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name=resp.model,
                model_provider="openai",
                metadata={"encoding_format": "float"},
                input_documents=[{"text": "hello world"}],
                output_value="[1 embedding(s) returned with size 1536]",
                token_metrics={"input_tokens": 2, "output_tokens": 0, "total_tokens": 2},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_embedding_string_array(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("embedding_string_array.yaml"):
            client = openai.OpenAI()
            resp = client.embeddings.create(input=["hello world", "hello again"], model="text-embedding-ada-002")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name=resp.model,
                model_provider="openai",
                metadata={"encoding_format": "float"},
                input_documents=[{"text": "hello world"}, {"text": "hello again"}],
                output_value="[2 embedding(s) returned with size 1536]",
                token_metrics={"input_tokens": 4, "output_tokens": 0, "total_tokens": 4},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_embedding_token_array(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("embedding_token_array.yaml"):
            client = openai.OpenAI()
            resp = client.embeddings.create(input=[1111, 2222, 3333], model="text-embedding-ada-002")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name=resp.model,
                model_provider="openai",
                metadata={"encoding_format": "float"},
                input_documents=[{"text": "[1111, 2222, 3333]"}],
                output_value="[1 embedding(s) returned with size 1536]",
                token_metrics={"input_tokens": 3, "output_tokens": 0, "total_tokens": 3},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_embedding_array_of_token_arrays(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("embedding_array_of_token_arrays.yaml"):
            client = openai.OpenAI()
            resp = client.embeddings.create(
                input=[[1111, 2222, 3333], [4444, 5555, 6666], [7777, 8888, 9999]], model="text-embedding-ada-002"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name=resp.model,
                model_provider="openai",
                metadata={"encoding_format": "float"},
                input_documents=[
                    {"text": "[1111, 2222, 3333]"},
                    {"text": "[4444, 5555, 6666]"},
                    {"text": "[7777, 8888, 9999]"},
                ],
                output_value="[3 embedding(s) returned with size 1536]",
                token_metrics={"input_tokens": 9, "output_tokens": 0, "total_tokens": 9},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 10, 0), reason="Embedding dimensions available in 1.10.0+"
    )
    def test_embedding_string_base64(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("embedding_b64.yaml"):
            client = openai.OpenAI()
            resp = client.embeddings.create(
                input="hello world", model="text-embedding-3-small", encoding_format="base64", dimensions=512
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name=resp.model,
                model_provider="openai",
                metadata={"encoding_format": "base64", "dimensions": 512},
                input_documents=[{"text": "hello world"}],
                output_value="[1 embedding(s) returned]",
                token_metrics={"input_tokens": 2, "output_tokens": 0, "total_tokens": 2},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    def test_deepseek_as_provider(self, openai, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("deepseek_completion.yaml"):
            client = openai.OpenAI(api_key="<not-a-real-key>", base_url="https://api.deepseek.com")

            client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant"},
                    {"role": "user", "content": "Hello"},
                ],
            )

        assert mock_llmobs_writer.enqueue.call_count == 1
        span_event = mock_llmobs_writer.enqueue._mock_call_args[0][0]

        assert span_event["name"] == "Deepseek.createChatCompletion"
        assert span_event["meta"]["model_provider"] == "deepseek"
        assert span_event["meta"]["model_name"] == "deepseek-chat"

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
    )
    def test_completion_stream_no_resp(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that None responses from streamed chat completions results in a finished span regardless."""
        client = openai.OpenAI()
        with mock.patch.object(client.completions, "_post", return_value=None):
            model = "ada"
            resp_model = model
            resp = client.completions.create(model=model, prompt="Hello world", stream=True)
            assert resp is None
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=[{"content": "Hello world"}],
                output_messages=[{"content": ""}],
                metadata={"stream": True},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
    )
    def test_chat_completion_stream_prompt_caching(
        self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        client = openai.OpenAI()
        """Test that prompt caching metrics are properly captured for streamed chat completions."""
        input_messages = [
            {
                "role": "system",
                "content": "you are not an expert software engineer " * 200,
            },
        ]
        request_args = {
            "model": "gpt-4o",
            "max_tokens": 100,
            "temperature": 0.1,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        with get_openai_vcr(subdirectory_name="v1").use_cassette(
            "chat_completion_stream_prompt_caching_cache_write.yaml"
        ):
            resp1 = client.chat.completions.create(
                messages=input_messages + [{"role": "user", "content": "What are the best practices for API design?"}],
                **request_args,
            )
            for chunk in resp1:
                if hasattr(chunk, "model"):
                    resp_model = chunk.model
        with get_openai_vcr(subdirectory_name="v1").use_cassette(
            "chat_completion_stream_prompt_caching_cache_read.yaml"
        ):
            resp2 = client.chat.completions.create(
                messages=input_messages + [{"role": "user", "content": "How should I structure my database schema?"}],
                **request_args,
            )
            for chunk in resp2:
                if hasattr(chunk, "model"):
                    resp_model = chunk.model

        spans = mock_tracer.pop_traces()
        span1, span2 = spans[0][0], spans[1][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        mock_llmobs_writer.enqueue.assert_has_calls(
            [
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span1,
                        model_name=resp_model,
                        model_provider="openai",
                        input_messages=input_messages
                        + [{"role": "user", "content": "What are the best practices for API design?"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={
                            "max_tokens": 100,
                            "temperature": 0.1,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                        },
                        token_metrics={
                            "input_tokens": 1421,
                            "output_tokens": 100,
                            "total_tokens": 1521,
                            "cache_read_input_tokens": 0,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span2,
                        model_name=resp_model,
                        model_provider="openai",
                        input_messages=input_messages
                        + [{"role": "user", "content": "How should I structure my database schema?"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={
                            "max_tokens": 100,
                            "temperature": 0.1,
                            "stream": True,
                            "stream_options": {"include_usage": True},
                        },
                        token_metrics={
                            "input_tokens": 1420,
                            "output_tokens": 100,
                            "total_tokens": 1520,
                            "cache_read_input_tokens": 1280,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
            ]
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 26), reason="Stream options only available openai >= 1.26"
    )
    def test_chat_stream_no_resp(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that None responses from streamed chat completions results in a finished span regardless."""
        client = openai.OpenAI()
        with mock.patch.object(client.chat.completions, "_post", return_value=None):
            model = "gpt-3.5-turbo"
            resp_model = model
            input_messages = [{"role": "user", "content": "Who won the world series in 2020?"}]
            resp = client.chat.completions.create(
                model=model,
                messages=input_messages,
                stream=True,
                user="ddtrace-test",
                stream_options={"include_usage": False},
            )
            assert resp is None
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"content": ""}],
                metadata={"stream": True, "stream_options": {"include_usage": False}, "user": "ddtrace-test"},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response(self, openai, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for response endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response.yaml"):
            model = "gpt-4.1"
            input_messages = multi_message_input
            client = openai.OpenAI()
            resp = client.responses.create(
                model=model, input=input_messages, top_p=0.9, max_output_tokens=100, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"role": "assistant", "content": output.content[0].text} for output in resp.output],
                metadata={
                    "top_p": 0.9,
                    "max_output_tokens": 100,
                    "user": "ddtrace-test",
                    "temperature": 1.0,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                },
                token_metrics={
                    "input_tokens": 53,
                    "output_tokens": 40,
                    "total_tokens": 93,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response_stream_tokens(self, openai, mock_llmobs_writer, mock_tracer):
        """Assert that streamed token chunk extraction logic works when options are not explicitly passed from user."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_stream.yaml"):
            model = "gpt-4.1"
            resp_model = model
            input_messages = "Hello world"
            expected_completion = "Hello! 🌍 How can I assist you today?"
            client = openai.OpenAI()
            resp = client.responses.create(model=model, input=input_messages, stream=True)
            for chunk in resp:
                resp_response = getattr(chunk, "response", {})
                resp_model = getattr(resp_response, "model", "")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=[{"content": input_messages, "role": "user"}],
                output_messages=[{"role": "assistant", "content": expected_completion}],
                metadata={
                    "stream": True,
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                },
                token_metrics={
                    "input_tokens": 9,
                    "output_tokens": 12,
                    "total_tokens": 21,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response_stream_incomplete(self, openai, mock_llmobs_writer, mock_tracer):
        client = openai.OpenAI()
        request_args = {
            "model": "gpt-4o",
            "max_output_tokens": 16,
            "temperature": 0.1,
            "stream": True,
        }
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_stream_incomplete.yaml"):
            resp1 = client.responses.create(
                input="Give me a multi paragraph narrative on the life of a car",
                **request_args,
            )
            for chunk in resp1:
                if hasattr(chunk, "response") and hasattr(chunk.response, "model"):
                    resp_model = chunk.response.model
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=[
                    {"content": "Give me a multi paragraph narrative on the life of a car", "role": "user"}
                ],
                output_messages=[
                    {
                        "role": "assistant",
                        "content": "In the bustling city of Detroit, a sleek, metallic blue sedan rolled off the",
                    }
                ],
                metadata={
                    "max_output_tokens": 16,
                    "temperature": 0.1,
                    "stream": True,
                    "top_p": 1.0,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                },
                token_metrics={
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response_function_call(self, openai, mock_llmobs_writer, mock_tracer, snapshot_tracer):
        """Test that function call response calls are recorded as LLMObs events correctly."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_function_call.yaml"):
            model = "gpt-4.1"
            client = openai.OpenAI()
            input_messages = "What is the weather like in Boston today?"
            resp = client.responses.create(
                tools=response_tool_function, model=model, input=input_messages, tool_choice="auto"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"role": "user", "content": input_messages}],
                output_messages=response_tool_function_expected_output,
                metadata={
                    "tool_choice": "auto",
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                },
                token_metrics={
                    "input_tokens": 75,
                    "output_tokens": 23,
                    "total_tokens": 98,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tool_definitions=[
                    {
                        "name": "get_current_weather",
                        "description": "Get the current weather in a given location",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "location": {
                                    "type": "string",
                                    "description": "The city and state, e.g. San Francisco, CA",
                                },
                                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                            },
                            "required": ["location", "unit"],
                        },
                    }
                ],
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response_function_call_stream(self, openai, mock_llmobs_writer, mock_tracer, snapshot_tracer):
        """Test that Response tool calls are recorded as LLMObs events correctly."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_function_call_streamed.yaml"):
            model = "gpt-4.1"
            input_messages = "What is the weather like in Boston today?"
            client = openai.OpenAI()
            resp = client.responses.create(
                tools=response_tool_function,
                model=model,
                input=input_messages,
                user="ddtrace-test",
                stream=True,
            )
            for chunk in resp:
                if hasattr(chunk, "response") and hasattr(chunk.response, "model"):
                    resp_model = chunk.response.model
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=[{"role": "user", "content": input_messages}],
                output_messages=response_tool_function_expected_output_streamed,
                metadata={
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "user": "ddtrace-test",
                    "stream": True,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                },
                tool_definitions=[
                    {
                        "name": "get_current_weather",
                        "description": "Get the current weather in a given location",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "location": {
                                    "type": "string",
                                    "description": "The city and state, e.g. San Francisco, CA",
                                },
                                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                            },
                            "required": ["location", "unit"],
                        },
                    }
                ],
                token_metrics={
                    "input_tokens": 75,
                    "output_tokens": 23,
                    "total_tokens": 98,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response_error(self, openai, mock_llmobs_writer, mock_tracer, snapshot_tracer):
        """Ensure erroneous llmobs records are emitted for response function call stream endpoints when configured."""
        with pytest.raises(Exception):
            with get_openai_vcr(subdirectory_name="v1").use_cassette("response_error.yaml"):
                model = "gpt-4.1"
                client = openai.OpenAI()
                input_messages = "Hello world"
                client.responses.create(model=model, input=input_messages, user="ddtrace-test")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=model,
                model_provider="openai",
                input_messages=[{"content": input_messages, "role": "user"}],
                output_messages=[{"content": ""}],
                metadata={"user": "ddtrace-test"},
                token_metrics={},
                error="openai.AuthenticationError",
                error_message="Error code: 401 - {'error': {'message': 'Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    async def test_response_async(self, openai, mock_llmobs_writer, mock_tracer):
        input_messages = multi_message_input
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response.yaml"):
            model = "gpt-4.1"
            input_messages = multi_message_input
            client = openai.AsyncOpenAI()
            resp = await client.responses.create(model=model, input=input_messages, top_p=0.9, max_output_tokens=100)

        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"role": "assistant", "content": output.content[0].text} for output in resp.output],
                metadata={
                    "temperature": 1.0,
                    "max_output_tokens": 100,
                    "top_p": 0.9,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                    "user": "ddtrace-test",
                },
                token_metrics={
                    "input_tokens": 53,
                    "output_tokens": 40,
                    "total_tokens": 93,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_responses_prompt_caching(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        client = openai.OpenAI()
        """Test that prompt caching metrics are properly captured for responses API"""
        model = "gpt-4o"
        base_input = "hello " * 1500
        with get_openai_vcr(subdirectory_name="v1").use_cassette("responses_prompt_caching_cache_write.yaml"):
            resp1 = client.responses.create(
                model=model,
                input=base_input + " count from 1 to 3",
                max_output_tokens=100,
                temperature=0.1,
                user="ddtrace-test",
            )
        with get_openai_vcr(subdirectory_name="v1").use_cassette("responses_prompt_caching_cache_read.yaml"):
            resp2 = client.responses.create(
                model=model,
                input=base_input + " count from 2 to 4",
                max_output_tokens=100,
                temperature=0.1,
                user="ddtrace-test",
            )
        spans = mock_tracer.pop_traces()
        span1, span2 = spans[0][0], spans[1][0]
        assert mock_llmobs_writer.enqueue.call_count == 2

        mock_llmobs_writer.enqueue.assert_has_calls(
            [
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span1,
                        model_name=resp1.model,
                        model_provider="openai",
                        input_messages=[{"role": "user", "content": base_input + " count from 1 to 3"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={
                            "max_output_tokens": 100,
                            "temperature": 0.1,
                            "top_p": 1.0,
                            "tool_choice": "auto",
                            "truncation": "disabled",
                            "text": {"format": {"type": "text"}},
                            "user": "ddtrace-test",
                        },
                        token_metrics={
                            "input_tokens": 1515,
                            "output_tokens": 14,
                            "total_tokens": 1529,
                            "cache_read_input_tokens": 0,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span2,
                        model_name=resp2.model,
                        model_provider="openai",
                        input_messages=[{"role": "user", "content": base_input + " count from 2 to 4"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={
                            "max_output_tokens": 100,
                            "temperature": 0.1,
                            "top_p": 1.0,
                            "tool_choice": "auto",
                            "truncation": "disabled",
                            "text": {"format": {"type": "text"}},
                            "user": "ddtrace-test",
                        },
                        token_metrics={
                            "input_tokens": 1515,
                            "output_tokens": 8,
                            "total_tokens": 1523,
                            "cache_read_input_tokens": 1390,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
            ],
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_responses_stream_prompt_caching(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        client = openai.OpenAI()
        """Test that prompt caching metrics are properly captured for streamed responses API"""
        base_input = "hello " * 1500
        request_args = {
            "model": "gpt-4o",
            "max_output_tokens": 100,
            "temperature": 0.1,
            "stream": True,
        }
        with get_openai_vcr(subdirectory_name="v1").use_cassette("responses_stream_prompt_caching_cache_write.yaml"):
            resp1 = client.responses.create(
                input=base_input + " count from 1 to 3",
                **request_args,
            )
            for chunk in resp1:
                if hasattr(chunk, "response") and hasattr(chunk.response, "model"):
                    resp_model = chunk.response.model
        with get_openai_vcr(subdirectory_name="v1").use_cassette("responses_stream_prompt_caching_cache_read.yaml"):
            resp2 = client.responses.create(
                input=base_input + " count from 2 to 4",
                **request_args,
            )
            for chunk in resp2:
                if hasattr(chunk, "response") and hasattr(chunk.response, "model"):
                    resp_model = chunk.response.model

        spans = mock_tracer.pop_traces()
        span1, span2 = spans[0][0], spans[1][0]
        assert mock_llmobs_writer.enqueue.call_count == 2
        mock_llmobs_writer.enqueue.assert_has_calls(
            [
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span1,
                        model_name=resp_model,
                        model_provider="openai",
                        input_messages=[{"role": "user", "content": base_input + " count from 1 to 3"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={
                            "max_output_tokens": 100,
                            "temperature": 0.1,
                            "top_p": 1.0,
                            "tool_choice": "auto",
                            "truncation": "disabled",
                            "text": {"format": {"type": "text"}},
                            "stream": True,
                        },
                        token_metrics={
                            "input_tokens": 1515,
                            "output_tokens": 14,
                            "total_tokens": 1529,
                            "cache_read_input_tokens": 0,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
                mock.call(
                    _expected_llmobs_llm_span_event(
                        span2,
                        model_name=resp_model,
                        model_provider="openai",
                        input_messages=[{"role": "user", "content": base_input + " count from 2 to 4"}],
                        output_messages=[{"role": "assistant", "content": mock.ANY}],
                        metadata={
                            "max_output_tokens": 100,
                            "temperature": 0.1,
                            "top_p": 1.0,
                            "tool_choice": "auto",
                            "truncation": "disabled",
                            "text": {"format": {"type": "text"}},
                            "stream": True,
                        },
                        token_metrics={
                            "input_tokens": 1515,
                            "output_tokens": 8,
                            "total_tokens": 1523,
                            "cache_read_input_tokens": 1390,
                            "reasoning_output_tokens": 0,
                        },
                        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
                    )
                ),
            ],
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_responses_reasoning_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        client = openai.OpenAI(base_url="http://127.0.0.1:9126/vcr/openai")

        stream = client.responses.create(
            input="If one plus a number is 10, what is the number?",
            model="o4-mini",
            reasoning={"effort": "medium", "summary": "detailed"},
            stream=True,
        )

        for event in stream:
            pass

        # special assertion on rough reasoning content
        span_event = mock_llmobs_writer.enqueue.call_args[0][0]
        reasoning_content = json.loads(span_event["meta"]["output"]["messages"][0]["content"])
        assistant_content = span_event["meta"]["output"]["messages"][1]["content"]
        assert reasoning_content["summary"] is not None
        assert assistant_content == "The number is 9, since 1 + x = 10 ⇒ x = 10 − 1 = 9."

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_responses_tool_message_input(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        client = openai.OpenAI(base_url="http://127.0.0.1:9126/vcr/openai")

        client.responses.create(
            input=[
                {"role": "user", "content": "What's the weather like in San Francisco?"},
                {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "get_weather",
                    "arguments": '{"location": "San Francisco, CA"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": '{"temperature": "72°F", "conditions": "sunny", "humidity": "65%"}',
                },
            ],
            model="gpt-4.1",
            temperature=0,
        )

        assert mock_llmobs_writer.enqueue.call_count == 1
        span_event = mock_llmobs_writer.enqueue.call_args[0][0]
        assert (
            span_event["meta"]["input"]["messages"][2]["tool_results"][0]["result"]
            == '{"temperature": "72°F", "conditions": "sunny", "humidity": "65%"}'
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 92), reason="Parse method only available in openai >= 1.92"
    )
    def test_chat_completion_parse(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        from typing import List

        from pydantic import BaseModel

        class Step(BaseModel):
            explanation: str
            output: str

        class MathResponse(BaseModel):
            steps: List[Step]
            final_answer: str

        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_parse.yaml"):
            client = openai.OpenAI()
            resp = client.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {"role": "system", "content": "You are a helpful math tutor."},
                    {"role": "user", "content": "solve 8x + 31 = 2"},
                ],
                response_format=MathResponse,
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[
                    {"role": "system", "content": "You are a helpful math tutor."},
                    {"role": "user", "content": "solve 8x + 31 = 2"},
                ],
                output_messages=[{"role": "assistant", "content": mock.ANY}],
                metadata={"response_format": "MathResponse"},
                token_metrics={
                    "input_tokens": 127,
                    "output_tokens": 93,
                    "total_tokens": 220,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 92), reason="Parse method only available in openai >= 1.92"
    )
    def test_response_parse(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        from typing import List

        from pydantic import BaseModel

        class Step(BaseModel):
            explanation: str
            output: str

        class MathResponse(BaseModel):
            steps: List[Step]
            final_answer: str

        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_parse.yaml"):
            client = openai.OpenAI()
            resp = client.responses.parse(
                model="gpt-4o-2024-08-06",
                input="solve 8x + 31 = 2",
                text_format=MathResponse,
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[
                    {"role": "user", "content": "solve 8x + 31 = 2"},
                ],
                output_messages=[{"role": "assistant", "content": mock.ANY}],
                metadata={
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {
                        "format": {
                            "name": "MathResponse",
                            "schema_": mock.ANY,
                            "type": "json_schema",
                            "strict": True,
                        },
                        "verbosity": "medium",
                    },
                },
                token_metrics={
                    "input_tokens": 113,
                    "output_tokens": 99,
                    "total_tokens": 212,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 80),
        reason="Full OpenAI MCP support only available with openai >= 1.80",
    )
    @mock.patch("openai._base_client.SyncAPIClient.post")
    def test_response_mcp_tool_call(
        self, mock_response_post, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        mock_response_post.return_value = mock_response_mcp_tool_call()

        client = openai.OpenAI()
        client.responses.create(
            model="gpt-5",
            tools=[
                {
                    "type": "mcp",
                    "server_label": "dice_roller",
                    "server_description": "Public dice-roller MCP server for testing.",
                    "server_url": "https://dice-rolling-mcp.vercel.app/sse",
                    "require_approval": "never",
                },
            ],
            input="Roll 2d4+1",
        )

        traces = mock_tracer.pop_traces()
        assert len(traces[0]) == 2

        response_span = traces[0][0]
        tool_span = traces[0][1]

        assert mock_llmobs_writer.enqueue.call_count == 2

        assert mock_llmobs_writer.enqueue.call_args_list[0][0][0] == _expected_llmobs_non_llm_span_event(
            tool_span,
            "tool",
            input_value=safe_json(
                {"notation": "2d4+1", "label": "2d4+1 roll", "verbose": True},
                ensure_ascii=False,
            ),
            output_value="You rolled 2d4+1 for 2d4+1 roll:\n🎲 Total: 8\n📊 Breakdown: 2d4:[3,4] + 1",
            metadata={"tool_id": "mcp_0f873afd7ff4f5b30168ffa1f7ddec81a0a114abda192da6b3"},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
        )
        assert tool_span.parent_id == response_span.span_id

        assert mock_llmobs_writer.enqueue.call_args_list[1][0][0] == _expected_llmobs_llm_span_event(
            response_span,
            model_name="gpt-5-2025-08-07",
            model_provider="openai",
            input_messages=[{"role": "user", "content": "Roll 2d4+1"}],
            output_messages=[
                {
                    "role": "reasoning",
                    "content": (
                        '{"summary": [], "encrypted_content": null, '
                        '"id": "rs_0f873afd7ff4f5b30168ffa1f5d91c81a0890e78a4873fbc1b"}'
                    ),
                },
                {
                    "tool_calls": [
                        {
                            "name": "dice_roll",
                            "type": "mcp_call",
                            "tool_id": "mcp_0f873afd7ff4f5b30168ffa1f7ddec81a0a114abda192da6b3",
                            "arguments": {"notation": "2d4+1", "label": "2d4+1 roll", "verbose": True},
                        }
                    ],
                    "tool_results": [
                        {
                            "name": "dice_roll",
                            "type": "mcp_tool_result",
                            "tool_id": "mcp_0f873afd7ff4f5b30168ffa1f7ddec81a0a114abda192da6b3",
                            "result": "You rolled 2d4+1 for 2d4+1 roll:\n🎲 Total: 8\n📊 Breakdown: 2d4:[3,4] + 1",
                        }
                    ],
                    "role": "assistant",
                },
                {"role": "assistant", "content": "You rolled 2d4+1:\n- Total: 8\n- Breakdown: 2d4 → [3, 4] + 1"},
            ],
            metadata={
                "temperature": 1.0,
                "top_p": 1.0,
                "tool_choice": "auto",
                "truncation": "disabled",
                "text": {"format": {"type": "text"}, "verbosity": "medium"},
            },
            token_metrics={
                "input_tokens": 642,
                "output_tokens": 206,
                "total_tokens": 848,
                "cache_read_input_tokens": 0,
                "reasoning_output_tokens": 128,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            tool_definitions=[
                {
                    "name": "dice_roll",
                    "description": (
                        "Roll dice using standard notation. IMPORTANT: For D&D advantage use '2d20kh1' (NOT '2d20')"
                    ),
                    "schema": {
                        "type": "object",
                        "properties": {
                            "notation": {
                                "type": "string",
                                "description": (
                                    'Dice notation. Examples: "1d20+5" (basic), "2d20kh1" (advantage), '
                                    '"2d20kl1" (disadvantage), "4d6kh3" (stats), "3d6!" (exploding), '
                                    '"4d6r1" (reroll 1s), "5d10>7" (successes)'
                                ),
                            },
                            "label": {
                                "type": "string",
                                "description": 'Optional label e.g., "Attack roll", "Fireball damage"',
                            },
                            "verbose": {
                                "type": "boolean",
                                "description": "Show detailed breakdown of individual dice results",
                            },
                        },
                        "required": ["notation"],
                        "additionalProperties": False,
                        "$schema": "http://json-schema.org/draft-07/schema#",
                    },
                }
            ],
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 87),
        reason="Reusable prompts only available in openai >= 1.87",
    )
    def test_response_with_prompt_tracking(self, openai, mock_llmobs_writer, mock_tracer):
        """Test that prompt metadata (id, version, variables) is captured for reusable prompts."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_with_prompt.yaml"):
            client = openai.OpenAI()
            client.responses.create(
                prompt={
                    "id": "pmpt_690b24669d8c81948acc0e98da10e6490190feb3a62eee0b",
                    "version": "4",
                    "variables": {"question": "What is machine learning?"},
                }
            )
        mock_tracer.pop_traces()
        assert mock_llmobs_writer.enqueue.call_count == 1

        assert_prompt_tracking(
            span_event=mock_llmobs_writer.enqueue.call_args[0][0],
            prompt_id="pmpt_690b24669d8c81948acc0e98da10e6490190feb3a62eee0b",
            prompt_version="4",
            variables={"question": "What is machine learning?"},
            expected_chat_template=[
                {"role": "developer", "content": "Direct & Conversational tone"},
                {"role": "user", "content": "You are a helpful assistant. Please answer this question: {{question}}"},
            ],
            expected_messages=[
                {"role": "developer", "content": "Direct & Conversational tone"},
                {
                    "role": "user",
                    "content": "You are a helpful assistant. Please answer this question: What is machine learning?",
                },
            ],
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 87),
        reason="Reusable prompts only available in openai >= 1.87",
    )
    def test_response_with_mixed_input_prompt_tracking_url_stripped(self, openai, mock_llmobs_writer, mock_tracer):
        """Test default behavior: image_url is stripped, file_id is preserved."""
        from openai.types.responses import ResponseInputFile
        from openai.types.responses import ResponseInputImage
        from openai.types.responses import ResponseInputText

        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_with_mixed_prompt_url_stripped.yaml"):
            client = openai.OpenAI()
            client.responses.create(
                prompt={
                    "id": "pmpt_69201db75c4c81959c01ea6987ab023c070192cd2843dec0",
                    "version": "2",
                    "variables": {
                        "user_message": ResponseInputText(type="input_text", text="Analyze these images and document"),
                        "user_image_1": ResponseInputImage(
                            type="input_image",
                            image_url="https://raw.githubusercontent.com/github/explore/main/topics/python/python.png",
                            detail="auto",
                        ),
                        "user_file": ResponseInputFile(
                            type="input_file",
                            file_url="https://www.berkshirehathaway.com/letters/2024ltr.pdf",
                        ),
                        "user_image_2": ResponseInputImage(
                            type="input_image",
                            file_id="file-BCuhT1HQ24kmtsuuzF1mh2",
                            detail="auto",
                        ),
                    },
                },
            )
        mock_tracer.pop_traces()
        assert mock_llmobs_writer.enqueue.call_count == 1

        assert_prompt_tracking(
            span_event=mock_llmobs_writer.enqueue.call_args[0][0],
            prompt_id="pmpt_69201db75c4c81959c01ea6987ab023c070192cd2843dec0",
            prompt_version="2",
            variables={
                "user_message": "Analyze these images and document",
                "user_image_1": "https://raw.githubusercontent.com/github/explore/main/topics/python/python.png",
                "user_file": "https://www.berkshirehathaway.com/letters/2024ltr.pdf",
                "user_image_2": "file-BCuhT1HQ24kmtsuuzF1mh2",
            },
            expected_chat_template=[
                {
                    "role": "user",
                    "content": (
                        "Analyze the following content from the user:\n\n"
                        "Text message: {{user_message}}\n"
                        "Image reference 1: [image]\n"
                        "Document reference: {{user_file}}\n"
                        "Image reference 2: {{user_image_2}}\n\n"
                        "Please provide a comprehensive analysis."
                    ),
                }
            ],
            expected_messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyze the following content from the user:\n\n"
                        "Text message: Analyze these images and document\n"
                        "Image reference 1: [image]\n"
                        "Document reference: https://www.berkshirehathaway.com/letters/2024ltr.pdf\n"
                        "Image reference 2: file-BCuhT1HQ24kmtsuuzF1mh2\n\n"
                        "Please provide a comprehensive analysis."
                    ),
                }
            ],
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 87),
        reason="Reusable prompts only available in openai >= 1.87",
    )
    def test_response_with_mixed_input_prompt_tracking_url_preserved(self, openai, mock_llmobs_writer, mock_tracer):
        """Test with include parameter: image_url is preserved."""
        from openai.types.responses import ResponseInputFile
        from openai.types.responses import ResponseInputImage
        from openai.types.responses import ResponseInputText

        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_with_mixed_prompt_url_preserved.yaml"):
            client = openai.OpenAI()
            client.responses.create(
                include=["message.input_image.image_url"],
                prompt={
                    "id": "pmpt_69201db75c4c81959c01ea6987ab023c070192cd2843dec0",
                    "version": "2",
                    "variables": {
                        "user_message": ResponseInputText(type="input_text", text="Analyze these images and document"),
                        "user_image_1": ResponseInputImage(
                            type="input_image",
                            image_url="https://raw.githubusercontent.com/github/explore/main/topics/python/python.png",
                            detail="auto",
                        ),
                        "user_file": ResponseInputFile(
                            type="input_file",
                            file_url="https://www.berkshirehathaway.com/letters/2024ltr.pdf",
                        ),
                        "user_image_2": ResponseInputImage(
                            type="input_image",
                            file_id="file-BCuhT1HQ24kmtsuuzF1mh2",
                            detail="auto",
                        ),
                    },
                },
            )
        mock_tracer.pop_traces()
        assert mock_llmobs_writer.enqueue.call_count == 1

        assert_prompt_tracking(
            span_event=mock_llmobs_writer.enqueue.call_args[0][0],
            prompt_id="pmpt_69201db75c4c81959c01ea6987ab023c070192cd2843dec0",
            prompt_version="2",
            variables={
                "user_message": "Analyze these images and document",
                "user_image_1": "https://raw.githubusercontent.com/github/explore/main/topics/python/python.png",
                "user_file": "https://www.berkshirehathaway.com/letters/2024ltr.pdf",
                "user_image_2": "file-BCuhT1HQ24kmtsuuzF1mh2",
            },
            expected_chat_template=[
                {
                    "role": "user",
                    "content": (
                        "Analyze the following content from the user:\n\n"
                        "Text message: {{user_message}}\n"
                        "Image reference 1: {{user_image_1}}\n"
                        "Document reference: {{user_file}}\n"
                        "Image reference 2: {{user_image_2}}\n\n"
                        "Please provide a comprehensive analysis."
                    ),
                }
            ],
            expected_messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyze the following content from the user:\n\n"
                        "Text message: Analyze these images and document\n"
                        "Image reference 1: "
                        "https://raw.githubusercontent.com/github/explore/main/topics/python/python.png\n"
                        "Document reference: https://www.berkshirehathaway.com/letters/2024ltr.pdf\n"
                        "Image reference 2: file-BCuhT1HQ24kmtsuuzF1mh2\n\n"
                        "Please provide a comprehensive analysis."
                    ),
                }
            ],
        )

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) < (1, 66), reason="Response options only available openai >= 1.66"
    )
    def test_response_reasoning_tokens(self, openai, mock_llmobs_writer, mock_tracer):
        """Test that reasoning tokens are captured in response endpoints."""
        with get_openai_vcr(subdirectory_name="v1").use_cassette("response_reasoning_tokens.yaml"):
            model = "gpt-5-mini"
            input_messages = multi_message_input
            client = openai.OpenAI()
            resp = client.responses.create(
                model=model, input=input_messages, max_output_tokens=500, user="ddtrace-test"
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1

        # Extract output messages including reasoning items
        output_messages = []
        for output in resp.output:
            if output.type == "reasoning":
                # Extract both reasoning and assistant messages
                reasoning_content = {
                    "summary": output.summary if hasattr(output, "summary") else [],
                    "encrypted_content": output.encrypted_content if hasattr(output, "encrypted_content") else "",
                    "id": output.id if hasattr(output, "id") else "",
                }
                output_messages.append({"role": "reasoning", "content": safe_json(reasoning_content)})
            elif output.type == "message":
                output_messages.append({"role": "assistant", "content": output.content[0].text})

        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=output_messages,
                metadata={
                    "max_output_tokens": 500,
                    "user": "ddtrace-test",
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}, "verbosity": "medium"},
                },
                token_metrics={
                    "input_tokens": 54,
                    "output_tokens": 164,
                    "total_tokens": 218,
                    "cache_read_input_tokens": 0,
                    "reasoning_output_tokens": 128,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.openai"},
            )
        )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>", _llmobs_agentless_enabled=True)],
)
@pytest.mark.skipif(parse_version(openai_module.version.VERSION) < (1, 0), reason="These tests are for openai >= 1.0")
def test_agentless_enabled_does_not_submit_metrics(openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
    """Ensure openai metrics are not emitted when agentless mode is enabled."""
    with get_openai_vcr(subdirectory_name="v1").use_cassette("completion.yaml"):
        model = "ada"
        client = openai.OpenAI()
        client.completions.create(
            model=model,
            prompt="Hello world",
            temperature=0.8,
            n=2,
            stop=".",
            max_tokens=10,
            user="ddtrace-test",
        )
    assert mock_llmobs_writer.enqueue.call_count == 1


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
