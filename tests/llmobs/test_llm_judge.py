"""Tests for LLMJudge evaluator."""

import json
from unittest import mock

import pytest

from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import _create_anthropic_client
from ddtrace.llmobs._evaluators.llm_judge import _create_azure_openai_client
from ddtrace.llmobs._evaluators.llm_judge import _create_bedrock_client
from ddtrace.llmobs._evaluators.llm_judge import _create_openai_client
from ddtrace.llmobs._evaluators.llm_judge import _create_vertexai_client
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult


class TestStructuredOutputTypes:
    def test_boolean_output_schema(self):
        output = BooleanStructuredOutput("Correctness check", reasoning=True)
        schema = output.to_json_schema()
        assert output.label == "boolean_eval"
        assert schema["properties"]["boolean_eval"]["type"] == "boolean"
        assert "reasoning" in schema["properties"]

    def test_score_output_schema(self):
        output = ScoreStructuredOutput("Quality", min_score=0.0, max_score=1.0, reasoning=True)
        schema = output.to_json_schema()
        assert output.label == "score_eval"
        assert schema["properties"]["score_eval"]["minimum"] == 0.0
        assert schema["properties"]["score_eval"]["maximum"] == 1.0

    def test_categorical_output_schema(self):
        output = CategoricalStructuredOutput("Sentiment", categories=["pos", "neg"])
        schema = output.to_json_schema()
        assert output.label == "categorical_eval"
        assert schema["properties"]["categorical_eval"]["enum"] == ["pos", "neg"]


class TestLLMJudge:
    def test_basic_evaluation(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return "The response is correct."

        judge = LLMJudge(client=mock_client, model="test-model", user_prompt="Evaluate: {{output_data}}")
        ctx = EvaluatorContext(input_data={}, output_data="test")
        assert judge.evaluate(ctx) == "The response is correct."

    def test_boolean_output_pass(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            assert json_schema["properties"]["boolean_eval"]["type"] == "boolean"
            return json.dumps({"boolean_eval": True, "reasoning": "Good"})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", reasoning=True, pass_when=True),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert isinstance(result, EvaluatorResult)
        assert result.value is True
        assert result.reasoning == "Good"
        assert result.assessment == "pass"

    def test_boolean_output_fail(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return json.dumps({"boolean_eval": False})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert result.value is False
        assert result.assessment == "fail"

    def test_score_output_pass(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return json.dumps({"score_eval": 0.85})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Rate: {{output_data}}",
            structured_output=ScoreStructuredOutput("Quality", min_score=0.0, max_score=1.0, min_threshold=0.7),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert result.value == 0.85
        assert result.assessment == "pass"

    def test_score_output_fail(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return json.dumps({"score_eval": 0.5})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Rate: {{output_data}}",
            structured_output=ScoreStructuredOutput("Quality", min_score=0.0, max_score=1.0, min_threshold=0.7),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert result.value == 0.5
        assert result.assessment == "fail"

    def test_categorical_output(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return json.dumps({"categorical_eval": "positive"})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Classify: {{output_data}}",
            structured_output=CategoricalStructuredOutput(
                "Sentiment", categories=["positive", "negative"], pass_values=["positive"]
            ),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="Great!"))

        assert result.value == "positive"
        assert result.assessment == "pass"

    def test_custom_json_schema_output(self):
        """Test structured_output with a custom JSON schema dict."""
        custom_schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "A brief summary"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "reasoning": {"type": "string"},
            },
            "required": ["summary", "keywords"],
            "additionalProperties": False,
        }

        def mock_client(provider, messages, json_schema, model, model_params):
            # Verify the custom schema is passed through
            assert json_schema == custom_schema
            return json.dumps({"summary": "Test summary", "keywords": ["a", "b"], "reasoning": "Because"})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Analyze: {{output_data}}",
            structured_output=custom_schema,
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert isinstance(result, EvaluatorResult)
        assert result.value == {"summary": "Test summary", "keywords": ["a", "b"], "reasoning": "Because"}
        assert result.reasoning == "Because"

    def test_template_rendering(self):
        captured = {}

        def mock_client(provider, messages, json_schema, model, model_params):
            captured["prompt"] = messages[-1]["content"]
            return "ok"

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Q: {{input_data.question}} A: {{output_data}} Tool: {{input_data.tool.name}}",
        )
        judge.evaluate(
            EvaluatorContext(
                input_data={"question": "What?", "tool": {"name": "search"}},
                output_data="Answer",
            )
        )

        assert captured["prompt"] == "Q: What? A: Answer Tool: search"

    def test_invalid_json_raises(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return "Not JSON"

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Check"),
        )
        with pytest.raises(ValueError, match="Invalid JSON"):
            judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

    def test_wrong_type_raises(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return json.dumps({"wrong_field": "not a bool"})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Check"),
        )
        with pytest.raises(ValueError, match="Expected boolean"):
            judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

    def test_requires_client_or_provider(self):
        with pytest.raises(ValueError, match="client.*provider"):
            LLMJudge(user_prompt="test")

    def test_optional_fields_not_set(self):
        def mock_client(provider, messages, json_schema, model, model_params):
            return json.dumps({"boolean_eval": True, "reasoning": "ignored"})

        judge = LLMJudge(
            client=mock_client,
            model="test-model",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Check"),  # No pass_when, reasoning=False
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))
        assert result.assessment is None
        assert result.reasoning is None


class TestOpenAIClient:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OpenAI API key not provided"):
            _create_openai_client()

    def test_missing_package_raises(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        with mock.patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="openai package required"):
                _create_openai_client()

    def test_client_call(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        mock_response = mock.MagicMock()
        mock_response.choices = [mock.MagicMock()]
        mock_response.choices[0].message.content = '{"result": true}'

        mock_openai_client = mock.MagicMock()
        mock_openai_client.chat.completions.create.return_value = mock_response

        mock_openai_module = mock.MagicMock()
        mock_openai_module.OpenAI.return_value = mock_openai_client

        with mock.patch.dict("sys.modules", {"openai": mock_openai_module}):
            client = _create_openai_client()
            result = client(
                provider="openai",
                messages=[{"role": "user", "content": "test"}],
                json_schema={"type": "object"},
                model="gpt-4o",
                model_params={"temperature": 0.5},
            )

        assert result == '{"result": true}'
        mock_openai_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["response_format"]["type"] == "json_schema"


class TestAzureOpenAIClient:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
        with pytest.raises(ValueError, match="Azure OpenAI API key not provided"):
            _create_azure_openai_client()

    def test_missing_endpoint_raises(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
        with pytest.raises(ValueError, match="Azure OpenAI endpoint not provided"):
            _create_azure_openai_client()

    def test_client_call(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")

        mock_response = mock.MagicMock()
        mock_response.choices = [mock.MagicMock()]
        mock_response.choices[0].message.content = '{"score": 0.9}'

        mock_azure_client = mock.MagicMock()
        mock_azure_client.chat.completions.create.return_value = mock_response

        mock_openai_module = mock.MagicMock()
        mock_openai_module.AzureOpenAI.return_value = mock_azure_client

        with mock.patch.dict("sys.modules", {"openai": mock_openai_module}):
            client = _create_azure_openai_client({"azure_deployment": "my-deployment"})
            result = client(
                provider="azure_openai",
                messages=[{"role": "user", "content": "test"}],
                json_schema=None,
                model="gpt-4o",
                model_params=None,
            )

        assert result == '{"score": 0.9}'
        call_kwargs = mock_azure_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "my-deployment"


class TestAnthropicClient:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Anthropic API key not provided"):
            _create_anthropic_client()

    def test_client_call_with_text_response(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        mock_text_block = mock.MagicMock()
        mock_text_block.text = '{"boolean_eval": true}'
        mock_text_block.json = None

        mock_response = mock.MagicMock()
        mock_response.content = [mock_text_block]

        mock_anthropic_client = mock.MagicMock()
        mock_anthropic_client.messages.create.return_value = mock_response

        mock_anthropic_module = mock.MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_anthropic_client

        with mock.patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = _create_anthropic_client()
            result = client(
                provider="anthropic",
                messages=[{"role": "system", "content": "You are a judge"}, {"role": "user", "content": "evaluate"}],
                json_schema={"type": "object", "properties": {"boolean_eval": {"type": "boolean"}}},
                model="claude-sonnet-4-20250514",
                model_params=None,
            )

        assert result == '{"boolean_eval": true}'
        call_kwargs = mock_anthropic_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are a judge"
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert "extra_headers" in call_kwargs
        assert "extra_body" in call_kwargs

    def test_client_removes_min_max_from_schema(self, monkeypatch):
        """Test that Anthropic client removes minimum/maximum from number properties."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        mock_text_block = mock.MagicMock()
        mock_text_block.text = '{"score_eval": 5}'
        mock_response = mock.MagicMock()
        mock_response.content = [mock_text_block]

        mock_anthropic_client = mock.MagicMock()
        mock_anthropic_client.messages.create.return_value = mock_response

        mock_anthropic_module = mock.MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_anthropic_client

        with mock.patch.dict("sys.modules", {"anthropic": mock_anthropic_module}):
            client = _create_anthropic_client()
            client(
                provider="anthropic",
                messages=[{"role": "user", "content": "rate"}],
                json_schema={
                    "type": "object",
                    "properties": {
                        "score_eval": {"type": "number", "minimum": 1, "maximum": 10, "description": "Score"}
                    },
                },
                model="claude-sonnet-4-20250514",
                model_params=None,
            )

        call_kwargs = mock_anthropic_client.messages.create.call_args[1]
        schema_sent = call_kwargs["extra_body"]["output_format"]["schema"]
        # minimum/maximum should be removed and added to description
        assert "minimum" not in schema_sent["properties"]["score_eval"]
        assert "maximum" not in schema_sent["properties"]["score_eval"]
        assert "(range: 1 to 10)" in schema_sent["properties"]["score_eval"]["description"]


class TestVertexAIClient:
    def test_missing_project_raises(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        with pytest.raises(ValueError, match="Google Cloud project not provided"):
            _create_vertexai_client()

    def test_client_call(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

        mock_part = mock.MagicMock()
        mock_part.text = '{"result": "positive"}'

        mock_content = mock.MagicMock()
        mock_content.parts = [mock_part]

        mock_candidate = mock.MagicMock()
        mock_candidate.content = mock_content

        mock_response = mock.MagicMock()
        mock_response.candidates = [mock_candidate]

        mock_model_instance = mock.MagicMock()
        mock_model_instance.generate_content.return_value = mock_response

        mock_generation_config = mock.MagicMock()

        mock_vertexai_module = mock.MagicMock()
        mock_generative_models = mock.MagicMock()
        mock_generative_models.GenerativeModel.return_value = mock_model_instance
        mock_generative_models.GenerationConfig.return_value = mock_generation_config

        with mock.patch.dict(
            "sys.modules",
            {
                "vertexai": mock_vertexai_module,
                "vertexai.generative_models": mock_generative_models,
            },
        ):
            client = _create_vertexai_client()
            result = client(
                provider="vertexai",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "evaluate"}],
                json_schema={"type": "object"},
                model="gemini-1.5-pro",
                model_params={"temperature": 0.2},
            )

        assert result == '{"result": "positive"}'
        mock_generative_models.GenerativeModel.assert_called_once_with("gemini-1.5-pro", system_instruction="Judge")


class TestBedrockClient:
    def test_missing_package_raises(self):
        with mock.patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(ImportError, match="boto3 package required"):
                _create_bedrock_client()

    def test_client_call(self):
        mock_bedrock_client = mock.MagicMock()
        mock_bedrock_client.converse.return_value = {"output": {"message": {"content": [{"text": '{"eval": true}'}]}}}

        mock_session = mock.MagicMock()
        mock_session.client.return_value = mock_bedrock_client

        mock_boto3 = mock.MagicMock()
        mock_boto3.Session.return_value = mock_session

        with mock.patch.dict("sys.modules", {"boto3": mock_boto3}):
            client = _create_bedrock_client({"region_name": "us-west-2"})
            result = client(
                provider="bedrock",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={"type": "object"},
                model="anthropic.claude-3-sonnet",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )

        assert result == '{"eval": true}'
        mock_bedrock_client.converse.assert_called_once()
        call_kwargs = mock_bedrock_client.converse.call_args[1]
        assert call_kwargs["modelId"] == "anthropic.claude-3-sonnet"
        assert call_kwargs["system"] == [{"text": "Judge"}]
        assert "inferenceConfig" in call_kwargs
