import mock
import openai as openai_module
import pytest

from ddtrace.internal.utils.version import parse_version
from tests.contrib.openai.utils import chat_completion_custom_functions
from tests.contrib.openai.utils import chat_completion_input_description
from tests.contrib.openai.utils import get_openai_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
@pytest.mark.skipif(parse_version(openai_module.version.VERSION) >= (1, 0), reason="These tests are for openai < 1.0")
class TestLLMObsOpenaiV0:
    def test_completion(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        with get_openai_vcr(subdirectory_name="v0").use_cassette("completion.yaml"):
            model = "ada"
            openai.Completion.create(
                model=model, prompt="Hello world", temperature=0.8, n=2, stop=".", max_tokens=10, user="ddtrace-test"
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v0").use_cassette("completion_streamed.yaml"):
            model = "ada"
            expected_completion = '! ... A page layouts page drawer? ... Interesting. The "Tools" is'
            resp = openai.Completion.create(model=model, prompt="Hello world", stream=True)
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
                token_metrics={"input_tokens": 2, "output_tokens": 16, "total_tokens": 18},
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            ),
        )

    def test_chat_completion(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion.yaml"):
            model = "gpt-3.5-turbo"
            input_messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Who won the world series in 2020?"},
                {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                {"role": "user", "content": "Where was it played?"},
            ]
            resp = openai.ChatCompletion.create(
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    async def test_chat_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_streamed.yaml"):
            with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
                model = "gpt-3.5-turbo"
                resp_model = model
                input_messages = [{"role": "user", "content": "Who won the world series in 2020?"}]
                mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
                expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
                resp = openai.ChatCompletion.create(
                    model=model, messages=input_messages, stream=True, user="ddtrace-test"
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
                metadata={"stream": True, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_chat_completion_function_call(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that function call chat completion calls are recorded as LLMObs events correctly."""
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_function_call.yaml"):
            model = "gpt-3.5-turbo"
            resp = openai.ChatCompletion.create(
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_chat_completion_function_call_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that function call chat completion calls are recorded as LLMObs events correctly."""
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_function_call_streamed.yaml"):
            with mock.patch("ddtrace.contrib.internal.openai.utils.encoding_for_model", create=True) as mock_encoding:
                model = "gpt-3.5-turbo"
                resp_model = model
                mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
                resp = openai.ChatCompletion.create(
                    model=model,
                    messages=[{"role": "user", "content": chat_completion_input_description}],
                    functions=chat_completion_custom_functions,
                    function_call="auto",
                    stream=True,
                    user="ddtrace-test",
                )
                for chunk in resp:
                    resp_model = chunk.model

        expected_output = '[function: extract_student_info]\n\n{"name":"David Nguyen","major":"Computer Science","school":"Stanford University","grades":3.8,"clubs":["Chess Club","South Asian Student Association"]}'  # noqa: E501
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp_model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[{"content": expected_output, "role": "assistant"}],
                metadata={"stream": True, "user": "ddtrace-test", "function_call": "auto"},
                token_metrics={"input_tokens": 63, "output_tokens": 33, "total_tokens": 96},
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_chat_completion_tool_call(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_tool_call.yaml"):
            resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": chat_completion_input_description}],
                tools=[{"type": "function", "function": chat_completion_custom_functions[0]}],
                tool_choice="auto",
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
                    "tool_id": "call_ukwJcJsOt7gOrv9xGRAntkZQ",
                    "type": "function",
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
                metadata={"tool_choice": "auto", "user": "ddtrace-test"},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_completion_error(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure erroneous llmobs records are emitted for completion endpoints when configured."""
        with pytest.raises(Exception):
            with get_openai_vcr(subdirectory_name="v0").use_cassette("completion_error.yaml"):
                model = "babbage-002"
                openai.Completion.create(
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
                error="openai.error.AuthenticationError",
                error_message="Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_chat_completion_error(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure erroneous llmobs records are emitted for chat completion endpoints when configured."""
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with pytest.raises(Exception):
            with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_error.yaml"):
                model = "gpt-3.5-turbo"
                input_messages = [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Who won the world series in 2020?"},
                    {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                    {"role": "user", "content": "Where was it played?"},
                ]
                openai.ChatCompletion.create(model=model, messages=input_messages, top_p=0.9, n=2, user="ddtrace-test")
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=model,
                model_provider="openai",
                input_messages=input_messages,
                output_messages=[{"content": ""}],
                metadata={"top_p": 0.9, "n": 2, "user": "ddtrace-test"},
                token_metrics={},
                error="openai.error.AuthenticationError",
                error_message="Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
@pytest.mark.skipif(parse_version(openai_module.version.VERSION) < (1, 0), reason="These tests are for openai >= 1.0")
class TestLLMObsOpenaiV1:
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            ),
        )

    def test_chat_completion(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion.yaml"):
            model = "gpt-3.5-turbo"
            input_messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Who won the world series in 2020?"},
                {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                {"role": "user", "content": "Where was it played?"},
            ]
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_chat_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
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
                        model=model, messages=input_messages, stream=True, user="ddtrace-test"
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
                metadata={"stream": True, "user": "ddtrace-test"},
                token_metrics={"input_tokens": 8, "output_tokens": 8, "total_tokens": 16},
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
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
                    "tool_id": "call_FJStsEjxdODw9tBmQRRkm6vY",
                    "type": "function",
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
                metadata={"user": "ddtrace-test"},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_chat_completion_error(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure erroneous llmobs records are emitted for chat completion endpoints when configured."""
        with pytest.raises(Exception):
            with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_error.yaml"):
                model = "gpt-3.5-turbo"
                client = openai.OpenAI()
                input_messages = [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Who won the world series in 2020?"},
                    {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
                    {"role": "user", "content": "Where was it played?"},
                ]
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
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
                tags={"ml_app": "<ml-app-name>"},
                integration="openai",
            )
        )

    def test_unserializable_param_is_handled(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with pytest.raises(Exception):
            model = "babbage-002"
            client = openai.OpenAI()
            client.completions.create(
                model=model,
                prompt="Hello world",
                temperature=0.8,
                n=object(),
                stop=".",
                max_tokens=10,
                user="ddtrace-test",
            )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        expected_span = _expected_llmobs_llm_span_event(
            span,
            model_name=model,
            model_provider="openai",
            input_messages=[{"content": "Hello world"}],
            output_messages=[{"content": ""}],
            metadata={"temperature": 0.8, "max_tokens": 10, "n": mock.ANY, "stop": ".", "user": "ddtrace-test"},
            token_metrics={},
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            tags={"ml_app": "<ml-app-name>"},
            integration="openai",
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected_span)
        actual_span = mock_llmobs_writer.enqueue.call_args[0][0]
        assert "[Unserializable object: <object object at " in actual_span["meta"]["metadata"]["n"]


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>", _llmobs_agentless_enabled=True)],
)
@pytest.mark.skipif(parse_version(openai_module.version.VERSION) < (1, 0), reason="These tests are for openai >= 1.0")
def test_agentless_enabled_does_not_submit_metrics(
    openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer, mock_metrics
):
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
    mock_metrics.assert_not_called()
    assert mock_metrics.increment.call_count == 0
    assert mock_metrics.distribution.call_count == 0
    assert mock_metrics.gauge.call_count == 0
