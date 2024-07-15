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
@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) >= (1, 0, 0), reason="These tests are for openai < 1.0"
)
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
                metadata={"temperature": 0.8, "max_tokens": 10},
                token_metrics={"input_tokens": 2, "output_tokens": 12, "total_tokens": 14},
                tags={"ml_app": "<ml-app-name>"},
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
                metadata={},
                token_metrics={"input_tokens": 2, "output_tokens": 16, "total_tokens": 18},
                tags={"ml_app": "<ml-app-name>"},
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
                model=model,
                messages=input_messages,
                top_p=0.9,
                n=2,
                user="ddtrace-test",
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
                metadata={},
                token_metrics={"input_tokens": 57, "output_tokens": 34, "total_tokens": 91},
                tags={"ml_app": "<ml-app-name>"},
            )
        )

    async def test_chat_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_streamed.yaml"):
            with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
                model = "gpt-3.5-turbo"
                resp_model = model
                input_messages = [{"role": "user", "content": "Who won the world series in 2020?"}]
                mock_encoding.return_value.encode.side_effect = lambda x: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
                expected_completion = "The Los Angeles Dodgers won the World Series in 2020."
                resp = openai.ChatCompletion.create(
                    model=model,
                    messages=input_messages,
                    stream=True,
                    user="ddtrace-test",
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
                metadata={},
                token_metrics={"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
                tags={"ml_app": "<ml-app-name>"},
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
        expected_output = "[function: {}]\n\n{}".format(
            resp.choices[0].message.function_call.name,
            resp.choices[0].message.function_call.arguments,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[{"content": expected_output, "role": "assistant"}],
                metadata={},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tags={"ml_app": "<ml-app-name>"},
            )
        )

    def test_chat_completion_function_call_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Test that function call chat completion calls are recorded as LLMObs events correctly."""
        if not hasattr(openai, "ChatCompletion"):
            pytest.skip("ChatCompletion not supported for this version of openai")
        with get_openai_vcr(subdirectory_name="v0").use_cassette("chat_completion_function_call_streamed.yaml"):
            with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
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
                metadata={},
                token_metrics={"input_tokens": 63, "output_tokens": 33, "total_tokens": 96},
                tags={"ml_app": "<ml-app-name>"},
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
        expected_output = '[tool: extract_student_info]\n\n{\n  "name": "David Nguyen",\n  "major": "computer science",\n  "school": "Stanford University",\n  "grades": 3.8,\n  "clubs": ["Chess Club", "South Asian Student Association"]\n}'  # noqa: E501
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[{"content": expected_output, "role": "assistant"}],
                metadata={},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tags={"ml_app": "<ml-app-name>"},
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
                metadata={"temperature": 0.8, "max_tokens": 10},
                token_metrics={},
                error="openai.error.AuthenticationError",
                error_message="Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>"},
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
                openai.ChatCompletion.create(
                    model=model,
                    messages=input_messages,
                    top_p=0.9,
                    n=2,
                    user="ddtrace-test",
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
                metadata={},
                token_metrics={},
                error="openai.error.AuthenticationError",
                error_message="Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>"},
            )
        )


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
@pytest.mark.skipif(
    parse_version(openai_module.version.VERSION) < (1, 0, 0), reason="These tests are for openai >= 1.0"
)
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
                metadata={"temperature": 0.8, "max_tokens": 10},
                token_metrics={"input_tokens": 2, "output_tokens": 12, "total_tokens": 14},
                tags={"ml_app": "<ml-app-name>"},
            )
        )

    def test_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        with get_openai_vcr(subdirectory_name="v1").use_cassette("completion_streamed.yaml"):
            with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
                with mock.patch("ddtrace.contrib.openai.utils._est_tokens") as mock_est:
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
                metadata={},
                token_metrics={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                tags={"ml_app": "<ml-app-name>"},
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
                model=model,
                messages=input_messages,
                top_p=0.9,
                n=2,
                user="ddtrace-test",
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
                metadata={},
                token_metrics={"input_tokens": 57, "output_tokens": 34, "total_tokens": 91},
                tags={"ml_app": "<ml-app-name>"},
            )
        )

    def test_chat_completion_stream(self, openai, ddtrace_global_config, mock_llmobs_writer, mock_tracer):
        """Ensure llmobs records are emitted for chat completion endpoints when configured.

        Also ensure the llmobs records have the correct tagging including trace/span ID for trace correlation.
        """
        with get_openai_vcr(subdirectory_name="v1").use_cassette("chat_completion_streamed.yaml"):
            with mock.patch("ddtrace.contrib.openai.utils.encoding_for_model", create=True) as mock_encoding:
                with mock.patch("ddtrace.contrib.openai.utils._est_tokens") as mock_est:
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
                metadata={},
                token_metrics={"input_tokens": 8, "output_tokens": 8, "total_tokens": 16},
                tags={"ml_app": "<ml-app-name>"},
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
        expected_output = "[function: {}]\n\n{}".format(
            resp.choices[0].message.function_call.name,
            resp.choices[0].message.function_call.arguments,
        )
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[{"content": expected_output, "role": "assistant"}],
                metadata={},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tags={"ml_app": "<ml-app-name>"},
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
        span = mock_tracer.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name=resp.model,
                model_provider="openai",
                input_messages=[{"content": chat_completion_input_description, "role": "user"}],
                output_messages=[
                    {
                        "content": "[tool: {}]\n\n{}".format(
                            resp.choices[0].message.tool_calls[0].function.name,
                            resp.choices[0].message.tool_calls[0].function.arguments,
                        ),
                        "role": "assistant",
                    }
                ],
                metadata={},
                token_metrics={"input_tokens": 157, "output_tokens": 57, "total_tokens": 214},
                tags={"ml_app": "<ml-app-name>"},
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
                metadata={"temperature": 0.8, "max_tokens": 10},
                token_metrics={},
                error="openai.AuthenticationError",
                error_message="Error code: 401 - {'error': {'message': 'Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>"},
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
                    model=model,
                    messages=input_messages,
                    top_p=0.9,
                    n=2,
                    user="ddtrace-test",
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
                metadata={},
                token_metrics={},
                error="openai.AuthenticationError",
                error_message="Error code: 401 - {'error': {'message': 'Incorrect API key provided: <not-a-r****key>. You can find your API key at https://platform.openai.com/account/api-keys.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}}",  # noqa: E501
                error_stack=span.get_tag("error.stack"),
                tags={"ml_app": "<ml-app-name>"},
            )
        )
