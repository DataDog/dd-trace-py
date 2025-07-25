import json

import mock
from mock import patch as mock_patch
import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.trace import Pin
from tests.contrib.botocore.bedrock_utils import _MODELS
from tests.contrib.botocore.bedrock_utils import _REQUEST_BODIES
from tests.contrib.botocore.bedrock_utils import BOTO_VERSION
from tests.contrib.botocore.bedrock_utils import bedrock_converse_args_with_system_and_tool
from tests.contrib.botocore.bedrock_utils import create_bedrock_converse_request
from tests.contrib.botocore.bedrock_utils import get_mock_response_data
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.utils import DummyTracer
from tests.utils import override_global_config


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsBedrock:
    @staticmethod
    def expected_llmobs_span_event(span, n_output, message=False, metadata=None, token_metrics=None):
        expected_input = [{"content": mock.ANY}]
        if message:
            expected_input = [{"content": mock.ANY, "role": "user"}]

        # Use empty dicts as defaults for _expected_llmobs_llm_span_event to avoid None issues
        expected_parameters = metadata if metadata is not None else {}
        expected_token_metrics = token_metrics if token_metrics is not None else None

        expected_event = _expected_llmobs_llm_span_event(
            span,
            model_name=span.get_tag("bedrock.request.model"),
            model_provider=span.get_tag("bedrock.request.model_provider"),
            input_messages=expected_input,
            output_messages=[{"content": mock.ANY} for _ in range(n_output)],
            metadata=expected_parameters,
            token_metrics=expected_token_metrics,
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

        # If parameters were not explicitly provided, use mock.ANY to match anything
        if metadata is None:
            expected_event["meta"]["metadata"] = mock.ANY
        if token_metrics is None:
            expected_event["metrics"] = mock.ANY

        return expected_event

    @classmethod
    def _test_llmobs_invoke(cls, provider, bedrock_client, mock_tracer, llmobs_events, cassette_name=None, n_output=1):
        if cassette_name is None:
            cassette_name = "%s_invoke.yaml" % provider
        body = _REQUEST_BODIES[provider]
        expected_metadata = None

        if provider == "cohere":
            body = {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": False,
                "num_generations": n_output,
            }
            expected_metadata = {"temperature": 0.9, "max_tokens": 10}

        with get_request_vcr().use_cassette(cassette_name):
            body, model = json.dumps(body), _MODELS[provider]
            if provider == "anthropic_message":
                # we do this to reuse a cassette which tests
                # cross-region inference
                model = "us." + model
            response = bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())
        span = mock_tracer.pop_traces()[0][0]

        assert len(llmobs_events) == 1
        assert llmobs_events[0] == cls.expected_llmobs_span_event(
            span, n_output, message="message" in provider, metadata=expected_metadata
        )
        LLMObs.disable()

    @classmethod
    def _test_llmobs_invoke_stream(
        cls, provider, bedrock_client, mock_tracer, llmobs_events, cassette_name=None, n_output=1
    ):
        if cassette_name is None:
            cassette_name = "%s_invoke_stream.yaml" % provider
        body = _REQUEST_BODIES[provider]
        expected_metadata = None

        if provider == "cohere":
            body = {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": True,
                "num_generations": n_output,
            }
            expected_metadata = {"temperature": 0.9, "max_tokens": 10}

        with get_request_vcr().use_cassette(cassette_name):
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
            for _ in response.get("body"):
                pass
        span = mock_tracer.pop_traces()[0][0]

        assert len(llmobs_events) == 1
        assert llmobs_events[0] == cls.expected_llmobs_span_event(
            span, n_output, message="message" in provider, metadata=expected_metadata
        )

    def test_llmobs_ai21_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("ai21", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_amazon_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("amazon", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("anthropic", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_message(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("anthropic_message", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_cohere_single_output_invoke(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke(
            "cohere", bedrock_client, mock_tracer, llmobs_events, cassette_name="cohere_invoke_single_output.yaml"
        )

    def test_llmobs_cohere_multi_output_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke(
            "cohere",
            bedrock_client,
            mock_tracer,
            llmobs_events,
            cassette_name="cohere_invoke_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke("meta", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_amazon_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke_stream("amazon", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke_stream("anthropic", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_anthropic_message_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke_stream("anthropic_message", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_cohere_single_output_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_tracer,
            llmobs_events,
            cassette_name="cohere_invoke_stream_single_output.yaml",
        )

    def test_llmobs_cohere_multi_output_invoke_stream(
        self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events
    ):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            mock_tracer,
            llmobs_events,
            cassette_name="cohere_invoke_stream_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events):
        self._test_llmobs_invoke_stream("meta", bedrock_client, mock_tracer, llmobs_events)

    def test_llmobs_only_patches_bedrock(self, ddtrace_global_config, llmobs_span_writer):
        llmobs_service.disable()

        with override_global_config(
            {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.llmobs"}
        ):
            llmobs_service.enable(integrations_enabled=True)
            mock_tracer = DummyTracer()
            # ensure we don't get spans for non-bedrock services
            from botocore.exceptions import ClientError
            import botocore.session

            session = botocore.session.get_session()
            sqs_client = session.create_client("sqs", region_name="us-east-1")
            pin = Pin.get_from(sqs_client)
            pin._override(sqs_client, tracer=mock_tracer)
            try:
                sqs_client.list_queues()
            except ClientError:
                pass
            assert mock_tracer.pop_traces() == []

        llmobs_service.disable()

    def test_llmobs_error(self, ddtrace_global_config, bedrock_client, mock_tracer, llmobs_events, request_vcr):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("meta_invoke_error.yaml"):
                body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
                response = bedrock_client.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())
        span = mock_tracer.pop_traces()[0][0]

        metadata = mock.ANY

        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name=span.get_tag("bedrock.request.model"),
            model_provider=span.get_tag("bedrock.request.model_provider"),
            input_messages=[{"content": mock.ANY}],
            metadata=metadata,
            output_messages=[{"content": ""}],
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse(cls, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse.yaml"):
            response = bedrock_client.converse(**request_params)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[
                {"role": "system", "content": request_params.get("system")[0]["text"]},
                {"role": "user", "content": request_params.get("messages")[0].get("content")[0].get("text")},
            ],
            output_messages=[
                {
                    "role": "assistant",
                    "content": response["output"]["message"]["content"][0]["text"],
                    "tool_calls": [
                        {
                            "arguments": {"concept": "distributed tracing"},
                            "name": "fetch_concept",
                            "tool_id": mock.ANY,
                        }
                    ],
                }
            ],
            metadata={
                "stop_reason": "tool_use",
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            token_metrics={
                "input_tokens": response["usage"]["inputTokens"],
                "output_tokens": response["usage"]["outputTokens"],
                "total_tokens": response["usage"]["totalTokens"],
            },
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_error(self, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        """Test error handling for the Bedrock Converse API."""
        import botocore

        user_content = "Explain the concept of distributed tracing in a simple way"
        request_params = create_bedrock_converse_request(user_message=user_content)

        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("bedrock_converse_error.yaml"):
                bedrock_client.converse(**request_params)

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[{"role": "user", "content": "Explain the concept of distributed tracing in a simple way"}],
            output_messages=[{"content": ""}],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature", 0.0),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens", 0),
            },
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_stream(cls, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        output_msg = ""
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse_stream.yaml"):
            response = bedrock_client.converse_stream(**request_params)
            for chunk in response["stream"]:
                if "contentBlockDelta" in chunk and "delta" in chunk["contentBlockDelta"]:
                    if "text" in chunk["contentBlockDelta"]["delta"]:
                        output_msg += chunk["contentBlockDelta"]["delta"]["text"]

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[
                {"role": "system", "content": request_params.get("system")[0]["text"]},
                {"role": "user", "content": request_params.get("messages")[0].get("content")[0].get("text")},
            ],
            output_messages=[
                {
                    "role": "assistant",
                    "content": output_msg,
                    "tool_calls": [
                        {
                            "arguments": {"concept": "distributed tracing"},
                            "name": "fetch_concept",
                            "tool_id": mock.ANY,
                        }
                    ],
                }
            ],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            token_metrics={
                "input_tokens": 259,
                "output_tokens": 64,
                "total_tokens": 323,
            },
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_modified_stream(cls, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        """
        Verify that LLM Obs tracing works even if stream chunks are modified mid-stream.
        """
        output_msg = ""
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse_stream.yaml"):
            response = bedrock_client.converse_stream(**request_params)
            for chunk in response["stream"]:
                if "contentBlockDelta" in chunk and "delta" in chunk["contentBlockDelta"]:
                    if "text" in chunk["contentBlockDelta"]["delta"]:
                        output_msg += chunk["contentBlockDelta"]["delta"]["text"]
                # delete keys from streamed chunk
                [chunk.pop(key) for key in list(chunk.keys())]

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            span,
            model_name="claude-3-sonnet-20240229-v1:0",
            model_provider="anthropic",
            input_messages=[
                {"role": "system", "content": request_params.get("system")[0]["text"]},
                {"role": "user", "content": request_params.get("messages")[0].get("content")[0].get("text")},
            ],
            output_messages=[
                {
                    "role": "assistant",
                    "content": output_msg,
                    "tool_calls": [
                        {
                            "arguments": {"concept": "distributed tracing"},
                            "name": "fetch_concept",
                            "tool_id": mock.ANY,
                        }
                    ],
                }
            ],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            token_metrics={
                "input_tokens": 259,
                "output_tokens": 64,
                "total_tokens": 323,
            },
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_prompt_caching(self, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        """Test that prompt caching metrics are properly captured for both cache creation and cache read."""
        large_system_prompt = "Software architecture guidelines: " + "bye " * 1024
        large_system_content = [
            {"text": large_system_prompt},
            {"cachePoint": {"type": "default"}},
        ]
        with request_vcr.use_cassette("bedrock_converse_prompt_caching.yaml"):
            _, _ = bedrock_client.converse(
                **create_bedrock_converse_request(
                    system=large_system_content,
                    user_message="What is a service",
                    modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                )
            ), bedrock_client.converse(
                **create_bedrock_converse_request(
                    system=large_system_content,
                    user_message="What is a ml app",
                    modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                )
            )
            assert len(llmobs_events) == 2
            spans = mock_tracer.pop_traces()
            span1, span2 = spans[0][0], spans[1][0]
            assert llmobs_events[0] == _expected_llmobs_llm_span_event(
                span1,
                model_name="claude-3-7-sonnet-20250219-v1:0",
                model_provider="anthropic",
                input_messages=[
                    {"role": "system", "content": large_system_prompt},
                    {"role": "system", "content": "[Unsupported content type: cachePoint]"},
                    {"role": "user", "content": "What is a service"},
                ],
                output_messages=[{"role": "assistant", "content": mock.ANY}],
                metadata={
                    "max_tokens": 1000,
                    "stop_reason": "end_turn",
                    "temperature": 0.7,
                },
                token_metrics={
                    "input_tokens": 1039,
                    "output_tokens": 264,
                    "total_tokens": 1303,
                    "cache_write_input_tokens": 1028,
                    "cache_read_input_tokens": 0,
                },
                tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
            )
            assert llmobs_events[1] == _expected_llmobs_llm_span_event(
                span2,
                model_name="claude-3-7-sonnet-20250219-v1:0",
                model_provider="anthropic",
                input_messages=[
                    {"role": "system", "content": large_system_prompt},
                    {"role": "system", "content": "[Unsupported content type: cachePoint]"},
                    {"role": "user", "content": "What is a ml app"},
                ],
                output_messages=[{"role": "assistant", "content": mock.ANY}],
                metadata={
                    "max_tokens": 1000,
                    "stop_reason": "end_turn",
                    "temperature": 0.7,
                },
                token_metrics={
                    "input_tokens": 1040,
                    "output_tokens": 185,
                    "total_tokens": 1225,
                    "cache_write_input_tokens": 0,
                    "cache_read_input_tokens": 1028,
                },
                tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
            )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_stream_prompt_caching(self, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        """Test that prompt caching metrics are properly captured for streamed converse responses."""
        large_system_prompt = "Software architecture guidelines: " + "hello " * 1024
        large_system_content = [
            {"text": large_system_prompt},
            {"cachePoint": {"type": "default"}},
        ]
        with request_vcr.use_cassette("bedrock_converse_stream_prompt_caching.yaml"):
            stream_1 = bedrock_client.converse_stream(
                **create_bedrock_converse_request(
                    system=large_system_content,
                    user_message="What is a service",
                    modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                )
            )
            for _ in stream_1["stream"]:
                pass
            stream_2 = bedrock_client.converse_stream(
                **create_bedrock_converse_request(
                    system=large_system_content,
                    user_message="What is a ml app",
                    modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                )
            )
            for _ in stream_2["stream"]:
                pass

            assert len(llmobs_events) == 2
            spans = mock_tracer.pop_traces()
            span1, span2 = spans[0][0], spans[1][0]

            assert llmobs_events[0] == _expected_llmobs_llm_span_event(
                span1,
                model_name="claude-3-7-sonnet-20250219-v1:0",
                model_provider="anthropic",
                input_messages=[
                    {"content": large_system_prompt, "role": "system"},
                    {"role": "system", "content": "[Unsupported content type: cachePoint]"},
                    {"content": "What is a service", "role": "user"},
                ],
                output_messages=[{"content": mock.ANY, "role": "assistant"}],
                metadata={
                    "max_tokens": 1000,
                    "temperature": 0.7,
                },
                token_metrics={
                    "input_tokens": 1039,
                    "output_tokens": 236,
                    "total_tokens": 1275,
                    "cache_write_input_tokens": 1028,
                    "cache_read_input_tokens": 0,
                },
                tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
            )
            assert llmobs_events[1] == _expected_llmobs_llm_span_event(
                span2,
                model_name="claude-3-7-sonnet-20250219-v1:0",
                model_provider="anthropic",
                input_messages=[
                    {"content": large_system_prompt, "role": "system"},
                    {"role": "system", "content": "[Unsupported content type: cachePoint]"},
                    {"content": "What is a ml app", "role": "user"},
                ],
                output_messages=[{"content": mock.ANY, "role": "assistant"}],
                metadata={
                    "max_tokens": 1000,
                    "temperature": 0.7,
                },
                token_metrics={
                    "input_tokens": 1040,
                    "output_tokens": 250,
                    "total_tokens": 1290,
                    "cache_write_input_tokens": 0,
                    "cache_read_input_tokens": 1028,
                },
                tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
            )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_tool_result_text(self, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            bedrock_client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                inferenceConfig={"temperature": 0.7, "topP": 0.9, "maxTokens": 1000, "stopSequences": []},
                messages=[
                    {"role": "user", "content": [{"toolResult": {"toolUseId": "foo", "content": [{"text": "bar"}]}}]}
                ],
            )

        assert len(llmobs_events) == 1
        assert llmobs_events[0]["meta"]["input"]["messages"] == [{"content": "bar", "role": "tool", "tool_id": "foo"}]

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_tool_result_json(self, bedrock_client, request_vcr, mock_tracer, llmobs_events):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            bedrock_client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                inferenceConfig={"temperature": 0.7, "topP": 0.9, "maxTokens": 1000, "stopSequences": []},
                messages=[
                    {
                        "role": "user",
                        "content": [{"toolResult": {"toolUseId": "foo", "content": [{"json": {"result": "bar"}}]}}],
                    }
                ],
            )

        assert len(llmobs_events) == 1
        assert llmobs_events[0]["meta"]["input"]["messages"] == [
            {"content": '{"result": "bar"}', "role": "tool", "tool_id": "foo"}
        ]

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_tool_result_json_non_text_or_json(
        self, bedrock_client, request_vcr, mock_tracer, llmobs_events
    ):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            bedrock_client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                inferenceConfig={"temperature": 0.7, "topP": 0.9, "maxTokens": 1000, "stopSequences": []},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": "foo",
                                    "content": [
                                        {
                                            "image": {
                                                "format": "png",
                                                "source": {"s3Location": {"uri": "s3://bucket/key"}},
                                            }
                                        }
                                    ],
                                }
                            }
                        ],
                    }
                ],
            )

        assert len(llmobs_events) == 1
        assert llmobs_events[0]["meta"]["input"]["messages"] == [
            {"content": "[Unsupported content type(s): image]", "role": "tool", "tool_id": "foo"}
        ]


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>"),
        dict(
            _llmobs_enabled=True,
            _llmobs_sample_rate=1.0,
            _llmobs_ml_app="<ml-app-name>",
            _llmobs_instrumented_proxy_urls="http://localhost:4000",
        ),
    ],
)
class TestLLMObsBedrockProxy:
    @staticmethod
    def expected_llmobs_span_event_proxy(span, n_output, message=False, metadata=None):
        expected_parameters = metadata if metadata is not None else mock.ANY
        return _expected_llmobs_non_llm_span_event(
            span,
            span_kind="workflow",
            input_value=mock.ANY,
            output_value=mock.ANY,
            metadata=expected_parameters,
            tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
        )

    @classmethod
    def _test_llmobs_invoke_proxy(
        cls,
        ddtrace_global_config,
        provider,
        bedrock_client,
        mock_tracer,
        llmobs_events,
        mock_invoke_model_http,
        n_output=1,
    ):
        body = _REQUEST_BODIES[provider]
        mock_invoke_model_response = get_mock_response_data(provider)
        if provider == "cohere":
            body = {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": False,
                "num_generations": n_output,
            }
        # mock out the completions response
        with mock_patch.object(bedrock_client, "_make_request") as mock_invoke_model_call:
            mock_invoke_model_call.return_value = mock_invoke_model_http, mock_invoke_model_response
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())

        if "_llmobs_instrumented_proxy_urls" in ddtrace_global_config:
            span = mock_tracer.pop_traces()[0][0]
            assert len(llmobs_events) == 1
            assert llmobs_events[0] == cls.expected_llmobs_span_event_proxy(
                span, n_output, message="message" in provider
            )
        else:
            span = mock_tracer.pop_traces()[0][0]
            assert len(llmobs_events) == 1
            assert llmobs_events[0]["meta"]["span.kind"] == "llm"

        LLMObs.disable()

    @classmethod
    def _test_llmobs_invoke_stream_proxy(
        cls,
        ddtrace_global_config,
        provider,
        bedrock_client,
        mock_tracer,
        llmobs_events,
        mock_invoke_model_http,
        n_output=1,
    ):
        body = _REQUEST_BODIES[provider]
        mock_invoke_model_response = get_mock_response_data(provider, stream=True)
        if provider == "cohere":
            body = {
                "prompt": "\n\nHuman: %s\n\nAssistant: Can you explain what a LLM chain is?",
                "temperature": 0.9,
                "p": 1.0,
                "k": 0,
                "max_tokens": 10,
                "stop_sequences": [],
                "stream": True,
                "num_generations": n_output,
            }
        # mock out the completions response
        with mock_patch.object(bedrock_client, "_make_request") as mock_invoke_model_call:
            mock_invoke_model_call.return_value = mock_invoke_model_http, mock_invoke_model_response
            body, model = json.dumps(body), _MODELS[provider]
            response = bedrock_client.invoke_model_with_response_stream(body=body, modelId=model)
            for _ in response.get("body"):
                pass

        if (
            "_llmobs_instrumented_proxy_urls" in ddtrace_global_config
            and ddtrace_global_config["_llmobs_instrumented_proxy_urls"]
        ):
            span = mock_tracer.pop_traces()[0][0]
            assert len(llmobs_events) == 1
            assert llmobs_events[0] == cls.expected_llmobs_span_event_proxy(
                span, n_output, message="message" in provider
            )
        else:
            span = mock_tracer.pop_traces()[0][0]
            assert len(llmobs_events) == 1
            assert llmobs_events[0]["meta"]["span.kind"] == "llm"

        LLMObs.disable()

    def test_llmobs_ai21_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "ai21",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_amazon_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "amazon",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "anthropic",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_message_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "anthropic_message",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_single_output_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_multi_output_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            n_output=2,
        )

    def test_llmobs_meta_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "meta",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            get_mock_response_data("meta"),
        )

    def test_llmobs_amazon_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "amazon",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "anthropic",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_message_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "anthropic_message",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_single_output_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_multi_output_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "meta",
            bedrock_client_proxy,
            mock_tracer_proxy,
            llmobs_events,
            mock_invoke_model_http,
        )

    def test_llmobs_error_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        mock_tracer_proxy,
        llmobs_events,
        mock_invoke_model_http_error,
        mock_invoke_model_response_error,
    ):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            # mock out the completions response
            with mock_patch.object(bedrock_client_proxy, "_make_request") as mock_invoke_model_call:
                mock_invoke_model_call.return_value = mock_invoke_model_http_error, mock_invoke_model_response_error
                body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
                response = bedrock_client_proxy.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())

        if (
            "_llmobs_instrumented_proxy_urls" in ddtrace_global_config
            and ddtrace_global_config["_llmobs_instrumented_proxy_urls"]
        ):
            span = mock_tracer_proxy.pop_traces()[0][0]
            assert len(llmobs_events) == 1
            assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
                span,
                "workflow",
                input_value=mock.ANY,
                output_value=mock.ANY,
                metadata={"temperature": 0.9, "max_tokens": 60},
                tags={"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>"},
                error="botocore.exceptions.ClientError",
                error_message=mock.ANY,
                error_stack=mock.ANY,
            )
        LLMObs.disable()
