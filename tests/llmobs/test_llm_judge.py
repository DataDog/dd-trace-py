"""Tests for LLMJudge evaluator."""

import json
from unittest import mock

import pytest

from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import _create_bedrock_client
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
        assert call_kwargs["inferenceConfig"] == {"temperature": 0.5, "maxTokens": 1024}

    def test_schema_strips_minimum_maximum(self):
        """Verify that minimum/maximum are removed from the schema sent to Bedrock."""
        mock_bedrock_client = mock.MagicMock()
        mock_bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"score_eval": 8}'}]}}
        }

        mock_session = mock.MagicMock()
        mock_session.client.return_value = mock_bedrock_client

        mock_boto3 = mock.MagicMock()
        mock_boto3.Session.return_value = mock_session

        with mock.patch.dict("sys.modules", {"boto3": mock_boto3}):
            client = _create_bedrock_client()
            client(
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
                model="anthropic.claude-3-sonnet",
                model_params=None,
            )

        call_kwargs = mock_bedrock_client.converse.call_args[1]
        schema_str = call_kwargs["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
        schema = json.loads(schema_str)
        score_prop = schema["properties"]["score_eval"]
        assert "minimum" not in score_prop
        assert "maximum" not in score_prop
        assert "(range: 1 to 10)" in score_prop["description"]

    def test_schema_strips_type_from_anyof(self):
        """Verify that 'type' is removed from properties using 'anyOf' in Bedrock schema."""
        mock_bedrock_client = mock.MagicMock()
        mock_bedrock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"categorical_eval": "positive"}'}]}}
        }

        mock_session = mock.MagicMock()
        mock_session.client.return_value = mock_bedrock_client

        mock_boto3 = mock.MagicMock()
        mock_boto3.Session.return_value = mock_session

        with mock.patch.dict("sys.modules", {"boto3": mock_boto3}):
            client = _create_bedrock_client()
            client(
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
                model="anthropic.claude-3-sonnet",
                model_params=None,
            )

        call_kwargs = mock_bedrock_client.converse.call_args[1]
        schema_str = call_kwargs["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
        schema = json.loads(schema_str)
        cat_prop = schema["properties"]["categorical_eval"]
        assert "type" not in cat_prop
        assert "anyOf" in cat_prop
