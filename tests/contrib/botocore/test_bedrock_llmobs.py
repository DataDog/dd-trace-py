import json

import mock
from mock import patch as mock_patch
import pytest

from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_span_kind
from tests.contrib.botocore.bedrock_utils import _MODELS
from tests.contrib.botocore.bedrock_utils import _REQUEST_BODIES
from tests.contrib.botocore.bedrock_utils import BOTO_VERSION
from tests.contrib.botocore.bedrock_utils import FETCH_CONCEPT_TOOL_DEFINITION
from tests.contrib.botocore.bedrock_utils import bedrock_converse_args_with_system_and_tool
from tests.contrib.botocore.bedrock_utils import create_bedrock_converse_request
from tests.contrib.botocore.bedrock_utils import get_mock_response_data
from tests.contrib.botocore.bedrock_utils import get_request_vcr
from tests.llmobs._utils import assert_llmobs_span_data
from tests.utils import override_global_config


BEDROCK_TAGS = {"service": "aws.bedrock-runtime", "ml_app": "<ml-app-name>", "integration": "bedrock"}


class TestLLMObsBedrock:
    @classmethod
    def _assert_llm_span(
        cls,
        span,
        n_output,
        model_id=None,
        input_message=False,
        output_message=False,
        metadata=None,
        metrics=None,
    ):
        expected_input = [{"content": mock.ANY, "role": "user"}] if input_message else [{"content": mock.ANY}]
        expected_output = [{"content": mock.ANY} for _ in range(n_output)] if output_message else []

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
            model_name=model_id or "",
            model_provider="amazon_bedrock",
            input_messages=expected_input,
            output_messages=expected_output,
            # Pass metadata only when explicitly provided so the matcher skips the
            # subset check (mirrors the prior ``mock.ANY`` whole-value escape hatch).
            metadata=metadata if metadata is not None else None,
            metrics=metrics if metrics is not None else None,
            tags=BEDROCK_TAGS,
        )

    @classmethod
    def _test_llmobs_invoke(cls, provider, bedrock_client, test_spans, cassette_name=None, n_output=1):
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        cls._assert_llm_span(
            spans[0],
            n_output,
            model_id=model,
            input_message="message" in provider,
            output_message=True,
            metadata=expected_metadata,
        )

    @classmethod
    def _test_llmobs_invoke_stream(cls, provider, bedrock_client, test_spans, cassette_name=None, n_output=1):
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        cls._assert_llm_span(
            spans[0],
            n_output,
            model_id=model,
            input_message="message" in provider,
            output_message=True,
            metadata=expected_metadata,
        )

    def test_llmobs_ai21_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke("ai21", bedrock_client, test_spans)

    def test_llmobs_amazon_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke("amazon", bedrock_client, test_spans)

    def test_llmobs_anthropic_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke("anthropic", bedrock_client, test_spans)

    def test_llmobs_anthropic_message(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke("anthropic_message", bedrock_client, test_spans)

    def test_llmobs_cohere_single_output_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke("cohere", bedrock_client, test_spans, cassette_name="cohere_invoke_single_output.yaml")

    def test_llmobs_cohere_multi_output_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke(
            "cohere",
            bedrock_client,
            test_spans,
            cassette_name="cohere_invoke_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke("meta", bedrock_client, test_spans)

    def test_llmobs_cohere_rerank_invoke(self, bedrock_client, bedrock_llmobs, test_spans):
        cassette_name = "cohere_rerank_invoke.yaml"
        model = "cohere.rerank-v3-5:0"
        prompt_data = "What is the capital of the United States?"
        documents = [
            "Carson City is the capital city of the American state of Nevada.",
            "The Commonwealth of the Northern Mariana Islands's capital is Saipan.",
        ]
        body = json.dumps({"query": prompt_data, "documents": documents, "api_version": 2, "top_n": 3})
        with get_request_vcr().use_cassette(cassette_name):
            response = bedrock_client.invoke_model(body=body, modelId=model)
            json.loads(response.get("body").read())
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        self._assert_llm_span(spans[0], 1, model_id=model)

    def test_llmobs_amazon_invoke_stream(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke_stream("amazon", bedrock_client, test_spans)

    def test_llmobs_anthropic_invoke_stream(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke_stream("anthropic", bedrock_client, test_spans)

    def test_llmobs_anthropic_message_invoke_stream(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke_stream("anthropic_message", bedrock_client, test_spans)

    def test_llmobs_cohere_single_output_invoke_stream(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            test_spans,
            cassette_name="cohere_invoke_stream_single_output.yaml",
        )

    def test_llmobs_cohere_multi_output_invoke_stream(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke_stream(
            "cohere",
            bedrock_client,
            test_spans,
            cassette_name="cohere_invoke_stream_multi_output.yaml",
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream(self, bedrock_client, bedrock_llmobs, test_spans):
        self._test_llmobs_invoke_stream("meta", bedrock_client, test_spans)

    def test_llmobs_only_patches_bedrock(self, tracer, bedrock_llmobs, test_spans):
        llmobs_service.disable()

        with override_global_config(
            {"_dd_api_key": "<not-a-real-api_key>", "_llmobs_ml_app": "<ml-app-name>", "service": "tests.llmobs"}
        ):
            llmobs_service.enable(integrations_enabled=True)
            # ensure we don't get spans for non-bedrock services
            from botocore.exceptions import ClientError
            import botocore.session

            session = botocore.session.get_session()
            sqs_client = session.create_client("sqs", region_name="us-east-1")
            try:
                sqs_client.list_queues()
            except ClientError:
                pass
            # Filter out urllib3 spans - we only care that SQS/botocore spans aren't generated
            traces = test_spans.pop_traces()
            non_urllib3_traces = [[s for s in t if s.name != "urllib3.request"] for t in traces]
            non_urllib3_traces = [t for t in non_urllib3_traces if t]  # Remove empty traces
            assert non_urllib3_traces == []

        llmobs_service.disable()

    def test_llmobs_error(self, bedrock_client, bedrock_llmobs, test_spans, request_vcr):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("meta_invoke_error.yaml"):
                body, model = json.dumps(_REQUEST_BODIES["meta"]), _MODELS["meta"]
                response = bedrock_client.invoke_model(body=body, modelId=model)
                json.loads(response.get("body").read())
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name=model,
            model_provider="amazon_bedrock",
            input_messages=[{"content": mock.ANY}],
            output_messages=[{"content": ""}],
            error={
                "type": spans[0].get_tag("error.type"),
                "message": spans[0].get_tag("error.message"),
                "stack": spans[0].get_tag("error.stack"),
            },
            tags=BEDROCK_TAGS,
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse.yaml"):
            response = bedrock_client.converse(**request_params)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="anthropic.claude-3-sonnet-20240229-v1:0",
            model_provider="amazon_bedrock",
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
                            "type": "toolUse",
                        }
                    ],
                }
            ],
            metadata={
                "stop_reason": "tool_use",
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            metrics={
                "input_tokens": response["usage"]["inputTokens"],
                "output_tokens": response["usage"]["outputTokens"],
                "total_tokens": response["usage"]["totalTokens"],
            },
            tool_definitions=[FETCH_CONCEPT_TOOL_DEFINITION],
            tags=BEDROCK_TAGS,
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_error(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
        """Test error handling for the Bedrock Converse API."""
        import botocore

        user_content = "Explain the concept of distributed tracing in a simple way"
        request_params = create_bedrock_converse_request(user_message=user_content)

        with pytest.raises(botocore.exceptions.ClientError):
            with request_vcr.use_cassette("bedrock_converse_error.yaml"):
                bedrock_client.converse(**request_params)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="anthropic.claude-3-sonnet-20240229-v1:0",
            model_provider="amazon_bedrock",
            input_messages=[{"role": "user", "content": "Explain the concept of distributed tracing in a simple way"}],
            output_messages=[{"content": ""}],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature", 0.0),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens", 0),
            },
            error={
                "type": spans[0].get_tag("error.type"),
                "message": spans[0].get_tag("error.message"),
                "stack": spans[0].get_tag("error.stack"),
            },
            tags=BEDROCK_TAGS,
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_stream(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
        output_msg = ""
        request_params = create_bedrock_converse_request(**bedrock_converse_args_with_system_and_tool)
        with request_vcr.use_cassette("bedrock_converse_stream.yaml"):
            response = bedrock_client.converse_stream(**request_params)
            for chunk in response["stream"]:
                if "contentBlockDelta" in chunk and "delta" in chunk["contentBlockDelta"]:
                    if "text" in chunk["contentBlockDelta"]["delta"]:
                        output_msg += chunk["contentBlockDelta"]["delta"]["text"]

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="anthropic.claude-3-sonnet-20240229-v1:0",
            model_provider="amazon_bedrock",
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
                            "type": "toolUse",
                        }
                    ],
                }
            ],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            metrics={
                "input_tokens": 259,
                "output_tokens": 64,
                "total_tokens": 323,
            },
            tool_definitions=[FETCH_CONCEPT_TOOL_DEFINITION],
            tags=BEDROCK_TAGS,
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_modified_stream(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="anthropic.claude-3-sonnet-20240229-v1:0",
            model_provider="amazon_bedrock",
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
                            "type": "toolUse",
                        }
                    ],
                }
            ],
            metadata={
                "temperature": request_params.get("inferenceConfig", {}).get("temperature"),
                "max_tokens": request_params.get("inferenceConfig", {}).get("maxTokens"),
            },
            metrics={
                "input_tokens": 259,
                "output_tokens": 64,
                "total_tokens": 323,
            },
            tool_definitions=[FETCH_CONCEPT_TOOL_DEFINITION],
            tags=BEDROCK_TAGS,
        )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_prompt_caching(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
        """Test that prompt caching metrics are properly captured for both cache creation and cache read."""
        large_system_prompt = "Software architecture guidelines: " + "bye " * 1024
        large_system_content = [
            {"text": large_system_prompt},
            {"cachePoint": {"type": "default"}},
        ]
        with request_vcr.use_cassette("bedrock_converse_prompt_caching.yaml"):
            _, _ = (
                bedrock_client.converse(
                    **create_bedrock_converse_request(
                        system=large_system_content,
                        user_message="What is a service",
                        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                    )
                ),
                bedrock_client.converse(
                    **create_bedrock_converse_request(
                        system=large_system_content,
                        user_message="What is a ml app",
                        modelId="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                    )
                ),
            )
            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 2
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="llm",
                model_name="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                model_provider="amazon_bedrock",
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
                metrics={
                    "input_tokens": 1039,
                    "output_tokens": 264,
                    "total_tokens": 1303,
                    "cache_write_input_tokens": 1028,
                    "cache_read_input_tokens": 0,
                },
                tags=BEDROCK_TAGS,
            )
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[1]),
                span_kind="llm",
                model_name="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                model_provider="amazon_bedrock",
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
                metrics={
                    "input_tokens": 1040,
                    "output_tokens": 185,
                    "total_tokens": 1225,
                    "cache_write_input_tokens": 0,
                    "cache_read_input_tokens": 1028,
                },
                tags=BEDROCK_TAGS,
            )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_stream_prompt_caching(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
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

            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 2

            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="llm",
                model_name="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                model_provider="amazon_bedrock",
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
                metrics={
                    "input_tokens": 1039,
                    "output_tokens": 236,
                    "total_tokens": 1275,
                    "cache_write_input_tokens": 1028,
                    "cache_read_input_tokens": 0,
                },
                tags=BEDROCK_TAGS,
            )
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[1]),
                span_kind="llm",
                model_name="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                model_provider="amazon_bedrock",
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
                metrics={
                    "input_tokens": 1040,
                    "output_tokens": 250,
                    "total_tokens": 1290,
                    "cache_write_input_tokens": 0,
                    "cache_read_input_tokens": 1028,
                },
                tags=BEDROCK_TAGS,
            )

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_tool_result_text(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
        import botocore

        with pytest.raises(botocore.exceptions.ClientError):
            bedrock_client.converse(
                modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                inferenceConfig={"temperature": 0.7, "topP": 0.9, "maxTokens": 1000, "stopSequences": []},
                messages=[
                    {"role": "user", "content": [{"toolResult": {"toolUseId": "foo", "content": [{"text": "bar"}]}}]}
                ],
            )

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert get_llmobs_input_messages(spans[0]) == [
            {"tool_results": [{"result": "bar", "tool_id": "foo", "type": "toolResult"}], "role": "user"}
        ]

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_tool_result_json(self, bedrock_client, request_vcr, bedrock_llmobs, test_spans):
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert get_llmobs_input_messages(spans[0]) == [
            {"tool_results": [{"result": '{"result": "bar"}', "tool_id": "foo", "type": "toolResult"}], "role": "user"}
        ]

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_tool_result_json_non_text_or_json(
        self, bedrock_client, request_vcr, bedrock_llmobs, test_spans
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert get_llmobs_input_messages(spans[0]) == [
            {
                "tool_results": [
                    {"result": "[Unsupported content type(s): image]", "tool_id": "foo", "type": "toolResult"}
                ],
                "role": "user",
            }
        ]

    @pytest.mark.skipif(BOTO_VERSION < (1, 34, 131), reason="Converse API not available until botocore 1.34.131")
    def test_llmobs_converse_guard_content_text(self, bedrock_client, request_vcr, test_spans, llmobs_events):
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
                                "guardContent": {
                                    "text": {
                                        "text": "give me my bill",
                                        "qualifiers": ["guard_content"],
                                    }
                                }
                            }
                        ],
                    }
                ],
            )

        assert len(llmobs_events) == 1
        assert llmobs_events[0]["meta"]["input"]["messages"] == [{"content": "give me my bill", "role": "user"}]


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        {},
        {"_llmobs_instrumented_proxy_urls": "http://localhost:4000"},
    ],
)
class TestLLMObsBedrockProxy:
    @classmethod
    def _test_llmobs_invoke_proxy(
        cls,
        ddtrace_global_config,
        provider,
        bedrock_client,
        test_spans,
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        if "_llmobs_instrumented_proxy_urls" in ddtrace_global_config:
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="workflow",
                input_value=mock.ANY,
                output_value=mock.ANY,
                tags=BEDROCK_TAGS,
            )
        else:
            assert get_llmobs_span_kind(spans[0]) == "llm"

    @classmethod
    def _test_llmobs_invoke_stream_proxy(
        cls,
        ddtrace_global_config,
        provider,
        bedrock_client,
        test_spans,
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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        if (
            "_llmobs_instrumented_proxy_urls" in ddtrace_global_config
            and ddtrace_global_config["_llmobs_instrumented_proxy_urls"]
        ):
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="workflow",
                input_value=mock.ANY,
                output_value=mock.ANY,
                tags=BEDROCK_TAGS,
            )
        else:
            assert get_llmobs_span_kind(spans[0]) == "llm"

    def test_llmobs_ai21_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "ai21",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_amazon_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "amazon",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "anthropic",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_message_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "anthropic_message",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_single_output_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_multi_output_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
            n_output=2,
        )

    def test_llmobs_meta_invoke_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_proxy(
            ddtrace_global_config,
            "meta",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_amazon_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "amazon",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "anthropic",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_anthropic_message_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "anthropic_message",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_single_output_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_cohere_multi_output_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "cohere",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
            n_output=2,
        )

    def test_llmobs_meta_invoke_stream_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
        mock_invoke_model_http,
    ):
        self._test_llmobs_invoke_stream_proxy(
            ddtrace_global_config,
            "meta",
            bedrock_client_proxy,
            test_spans,
            mock_invoke_model_http,
        )

    def test_llmobs_error_proxy(
        self,
        ddtrace_global_config,
        bedrock_client_proxy,
        bedrock_llmobs,
        test_spans,
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
            spans = [s for trace in test_spans.pop_traces() for s in trace]
            assert len(spans) == 1
            assert_llmobs_span_data(
                _get_llmobs_data_metastruct(spans[0]),
                span_kind="workflow",
                input_value=mock.ANY,
                output_value=mock.ANY,
                metadata={"temperature": 0.9, "max_tokens": 60},
                tags=BEDROCK_TAGS,
                error={
                    "type": "botocore.exceptions.ClientError",
                    "message": mock.ANY,
                    "stack": mock.ANY,
                },
            )


def test_shadow_tags_invoke_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on Bedrock spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.bedrock import BedrockIntegration

    integration = BedrockIntegration(MagicMock())

    ctx = MagicMock()
    ctx.get_item.side_effect = lambda key: {
        "llmobs.proxy_request": None,
        "llmobs.usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        "model_id": "anthropic.claude-v2",
        "model_name": "anthropic.claude-v2",
    }.get(key)

    with tracer.trace("botocore.command") as span:
        integration._set_apm_shadow_tags(span, [ctx], {}, response=None, operation="")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "anthropic.claude-v2"
    assert span.get_tag("_dd.llmobs.model_provider") == "bedrock"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 20
    assert span.get_metric("_dd.llmobs.output_tokens") == 10
    assert span.get_metric("_dd.llmobs.total_tokens") == 30
