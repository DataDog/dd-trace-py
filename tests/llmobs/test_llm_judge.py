"""Tests for LLMJudge evaluator."""

import json
from unittest import mock

import pytest

from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import _create_azure_openai_client
from ddtrace.llmobs._evaluators.llm_judge import _create_bedrock_client
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from tests.llmobs._utils import get_azure_openai_vcr
from tests.llmobs._utils import get_bedrock_vcr


BEDROCK_CLIENT_OPTIONS = {
    "aws_access_key_id": "testing",
    "aws_secret_access_key": "testing",
    "region_name": "us-east-1",
}


@pytest.fixture(scope="session")
def bedrock_vcr():
    yield get_bedrock_vcr()


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
        output = CategoricalStructuredOutput(categories={"pos": "Positive sentiment", "neg": "Negative sentiment"})
        schema = output.to_json_schema()
        assert output.label == "categorical_eval"
        assert schema["properties"]["categorical_eval"]["anyOf"] == [
            {"const": "pos", "description": "Positive sentiment"},
            {"const": "neg", "description": "Negative sentiment"},
        ]


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
                categories={"positive": "Positive sentiment", "negative": "Negative sentiment"},
                pass_values=["positive"],
            ),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="Great!"))

        assert result.value == "positive"
        assert result.assessment == "pass"

    def test_custom_json_schema_output(self):
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
            structured_output=BooleanStructuredOutput("Check"),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))
        assert result.assessment is None
        assert result.reasoning is None


AZURE_OPENAI_CLIENT_OPTIONS = {
    "api_key": "testing",
    "azure_endpoint": "https://test.openai.azure.com",
    "api_version": "2024-10-21",
    "azure_deployment": "gpt-4o",
}


@pytest.fixture(scope="session")
def azure_openai_vcr():
    yield get_azure_openai_vcr()


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

    def test_missing_openai_package_raises(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.openai.azure.com")
        with mock.patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="openai package required"):
                _create_azure_openai_client()

    def test_client_call(self, azure_openai_vcr):
        with azure_openai_vcr.use_cassette("azure_openai_chat_completion_boolean.yaml"):
            client = _create_azure_openai_client(AZURE_OPENAI_CLIENT_OPTIONS)
            result = client(
                provider="azure_openai",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={
                    "type": "object",
                    "properties": {"boolean_eval": {"type": "boolean"}},
                    "required": ["boolean_eval"],
                    "additionalProperties": False,
                },
                model="gpt-4o",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )
        assert result == '{"boolean_eval": true}'

    def test_client_call_with_score_schema(self, azure_openai_vcr):
        with azure_openai_vcr.use_cassette("azure_openai_chat_completion_score.yaml"):
            client = _create_azure_openai_client(AZURE_OPENAI_CLIENT_OPTIONS)
            result = client(
                provider="azure_openai",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={
                    "type": "object",
                    "properties": {
                        "score_eval": {"type": "number", "minimum": 1, "maximum": 10, "description": "Score"},
                    },
                    "required": ["score_eval"],
                    "additionalProperties": False,
                },
                model="gpt-4o",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )
        parsed = json.loads(result)
        assert parsed["score_eval"] == 8

    def test_client_call_with_categorical_schema(self, azure_openai_vcr):
        with azure_openai_vcr.use_cassette("azure_openai_chat_completion_categorical.yaml"):
            client = _create_azure_openai_client(AZURE_OPENAI_CLIENT_OPTIONS)
            result = client(
                provider="azure_openai",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={
                    "type": "object",
                    "properties": {
                        "categorical_eval": {
                            "type": "string",
                            "anyOf": [
                                {"const": "positive", "description": "Positive sentiment"},
                                {"const": "negative", "description": "Negative sentiment"},
                            ],
                        },
                    },
                    "required": ["categorical_eval"],
                    "additionalProperties": False,
                },
                model="gpt-4o",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )
        parsed = json.loads(result)
        assert parsed["categorical_eval"] == "positive"

    def test_llmjudge_with_azure_openai_provider(self, azure_openai_vcr):
        with azure_openai_vcr.use_cassette("azure_openai_chat_completion_boolean.yaml"):
            judge = LLMJudge(
                provider="azure_openai",
                model="gpt-4o",
                user_prompt="Evaluate: {{output_data}}",
                structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
                client_options=AZURE_OPENAI_CLIENT_OPTIONS,
            )
            result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))
        assert isinstance(result, EvaluatorResult)
        assert result.value is True
        assert result.assessment == "pass"


class TestBedrockClient:
    def test_missing_package_raises(self):
        with mock.patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(ImportError, match="boto3 package required"):
                _create_bedrock_client()

    def test_client_call(self, bedrock_vcr):
        with bedrock_vcr.use_cassette("bedrock_converse_boolean.yaml"):
            client = _create_bedrock_client(BEDROCK_CLIENT_OPTIONS)
            result = client(
                provider="bedrock",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={"type": "object", "properties": {"eval": {"type": "boolean"}}, "required": ["eval"]},
                model="anthropic.claude-3-sonnet-20240229-v1:0",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )

        assert result == '{"eval": true}'

    def test_schema_strips_minimum_maximum(self, bedrock_vcr):
        with bedrock_vcr.use_cassette("bedrock_converse_score.yaml"):
            client = _create_bedrock_client(BEDROCK_CLIENT_OPTIONS)
            result = client(
                provider="bedrock",
                messages=[{"role": "user", "content": "rate this"}],
                json_schema={
                    "type": "object",
                    "properties": {
                        "score_eval": {
                            "type": "number",
                            "description": "Quality score",
                            "minimum": 1,
                            "maximum": 10,
                        }
                    },
                    "required": ["score_eval"],
                },
                model="anthropic.claude-3-sonnet-20240229-v1:0",
                model_params=None,
            )

        parsed = json.loads(result)
        assert parsed["score_eval"] == 8

    def test_schema_strips_type_from_anyof(self, bedrock_vcr):
        with bedrock_vcr.use_cassette("bedrock_converse_categorical.yaml"):
            client = _create_bedrock_client(BEDROCK_CLIENT_OPTIONS)
            result = client(
                provider="bedrock",
                messages=[{"role": "user", "content": "classify this"}],
                json_schema={
                    "type": "object",
                    "properties": {
                        "categorical_eval": {
                            "type": "string",
                            "anyOf": [
                                {"const": "positive", "description": "Positive sentiment"},
                                {"const": "negative", "description": "Negative sentiment"},
                            ],
                        }
                    },
                    "required": ["categorical_eval"],
                },
                model="anthropic.claude-3-sonnet-20240229-v1:0",
                model_params=None,
            )

        parsed = json.loads(result)
        assert parsed["categorical_eval"] == "positive"
