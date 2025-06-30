import mock
import openai as openai_module
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import safe_json
from tests.contrib.openai.utils import chat_completion_custom_functions
from tests.contrib.openai.utils import chat_completion_input_description
from tests.contrib.openai.utils import get_openai_vcr
from tests.contrib.openai.utils import mock_openai_chat_completions_response
from tests.contrib.openai.utils import mock_openai_completions_response
from tests.contrib.openai.utils import multi_message_input
from tests.contrib.openai.utils import response_tool_function
from tests.contrib.openai.utils import response_tool_function_expected_output
from tests.contrib.openai.utils import response_tool_function_expected_output_streamed
from tests.contrib.openai.utils import tool_call_expected_output
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


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
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span.kind"] == "llm"

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
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span.kind"] == "llm"

    @pytest.mark.skipif(
        parse_version(openai_module.version.VERSION) >= (1, 60),
        reason="latest openai versions use modified azure requests",
    )
    def test_completion_azure(
        self, openai, azure_openai_config, ddtrace_global_config, mock_llmobs_writer, mock_tracer
    ):
        prompt = "why do some languages have words that can't directly be translated to other languages?"
        expected_output = '". The answer is that languages are not just a collection of words, but also a collection of cultural'  # noqa: E501
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
        expected_output = '". The answer is that languages are not just a collection of words, but also a collection of cultural'  # noqa: E501
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
            with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
                with mock.patch("ddtrace.contrib.internal.openai.utils._est_tokens") as mock_est:
                    mock_encoding.return_value.encode.side_effect = lambda x: [1, 2]
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
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span.kind"] == "llm"

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
        assert mock_llmobs_writer.enqueue.call_args_list[1].args[0]["meta"]["span.kind"] == "llm"

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
            with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
                with mock.patch("ddtrace.contrib.internal.openai.utils._est_tokens") as mock_est:
                    mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8]
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
                    "tools": [],
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                    "reasoning_tokens": 0,
                },
                token_metrics={"input_tokens": 53, "output_tokens": 40, "total_tokens": 93},
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
                    "tools": [],
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                    "reasoning_tokens": 0,
                },
                token_metrics={"input_tokens": 9, "output_tokens": 12, "total_tokens": 21},
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
                    "tools": [
                        {
                            "type": "function",
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
                                "required": ["location", "unit"],
                            },
                            "strict": True,
                        }
                    ],
                    "tool_choice": "auto",
                    "temperature": 1.0,
                    "top_p": 1.0,
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                    "reasoning_tokens": 0,
                },
                token_metrics={"input_tokens": 75, "output_tokens": 23, "total_tokens": 98},
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
                    "tools": [
                        {
                            "type": "function",
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
                                "required": ["location", "unit"],
                            },
                            "strict": True,
                        }
                    ],
                    "user": "ddtrace-test",
                    "stream": True,
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                    "reasoning_tokens": 0,
                },
                token_metrics={"input_tokens": 75, "output_tokens": 23, "total_tokens": 98},
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
                    "tools": [],
                    "tool_choice": "auto",
                    "truncation": "disabled",
                    "text": {"format": {"type": "text"}},
                    "user": "ddtrace-test",
                    "reasoning_tokens": 0,
                },
                token_metrics={"input_tokens": 53, "output_tokens": 40, "total_tokens": 93},
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
