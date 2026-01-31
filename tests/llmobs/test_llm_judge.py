"""Tests for LLMJudge evaluator."""

import json

import pytest

from ddtrace.llmobs._evaluators.llm_judge import BooleanOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreOutput
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs.types import JSONType


class TestStructuredOutputTypes:
    def test_boolean_output_schema(self):
        output = BooleanOutput("Correctness check", reasoning=True)
        schema = output.to_json_schema()
        assert output.label == "boolean_eval"
        assert schema["properties"]["boolean_eval"]["type"] == "boolean"
        assert "reasoning" in schema["properties"]

    def test_score_output_schema(self):
        output = ScoreOutput("Quality", min_score=0.0, max_score=1.0, reasoning=True)
        schema = output.to_json_schema()
        assert output.label == "score_eval"
        assert schema["properties"]["score_eval"]["minimum"] == 0.0
        assert schema["properties"]["score_eval"]["maximum"] == 1.0

    def test_categorical_output_schema(self):
        output = CategoricalOutput("Sentiment", categories=["pos", "neg"])
        schema = output.to_json_schema()
        assert output.label == "categorical_eval"
        assert schema["properties"]["categorical_eval"]["enum"] == ["pos", "neg"]


class TestLLMJudge:
    def test_basic_evaluation(self):
        def mock_client(messages, json_schema=None):
            return "The response is correct."

        judge = LLMJudge(client=mock_client, user_prompt="Evaluate: {{output_data}}")
        ctx = EvaluatorContext(input_data={}, output_data="test")
        assert judge.evaluate(ctx) == "The response is correct."

    def test_boolean_output_pass(self):
        def mock_client(messages, json_schema=None):
            assert json_schema["properties"]["boolean_eval"]["type"] == "boolean"
            return json.dumps({"boolean_eval": True, "reasoning": "Good"})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanOutput("Correctness", reasoning=True, pass_when=True),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert isinstance(result, EvaluatorResult)
        assert result.value is True
        assert result.reasoning == "Good"
        assert result.assessment == "pass"

    def test_boolean_output_fail(self):
        def mock_client(messages, json_schema=None):
            return json.dumps({"boolean_eval": False})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanOutput("Correctness", pass_when=True),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert result.value is False
        assert result.assessment == "fail"

    def test_score_output_pass(self):
        def mock_client(messages, json_schema=None):
            return json.dumps({"score_eval": 0.85})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Rate: {{output_data}}",
            structured_output=ScoreOutput("Quality", min_score=0.0, max_score=1.0, min_threshold=0.7),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert result.value == 0.85
        assert result.assessment == "pass"

    def test_score_output_fail(self):
        def mock_client(messages, json_schema=None):
            return json.dumps({"score_eval": 0.5})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Rate: {{output_data}}",
            structured_output=ScoreOutput("Quality", min_score=0.0, max_score=1.0, min_threshold=0.7),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert result.value == 0.5
        assert result.assessment == "fail"

    def test_categorical_output(self):
        def mock_client(messages, json_schema=None):
            return json.dumps({"categorical_eval": "positive"})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Classify: {{output_data}}",
            structured_output=CategoricalOutput(
                "Sentiment", categories=["positive", "negative"], pass_values=["positive"]
            ),
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="Great!"))

        assert result.value == "positive"
        assert result.assessment == "pass"

    def test_json_output(self):
        def mock_client(messages, json_schema=None):
            assert json_schema is None
            return json.dumps({"key": "value", "nested": {"a": 1}})

        judge = LLMJudge(client=mock_client, user_prompt="Analyze: {{output_data}}", structured_output=JSONType)
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

        assert isinstance(result, dict)
        assert result["key"] == "value"

    def test_template_rendering(self):
        captured = {}

        def mock_client(messages, json_schema=None):
            captured["prompt"] = messages[-1]["content"]
            return "ok"

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Q: {{input_data.question}} A: {{output_data}} Tool: {{input_data.tool.name}}",
        )
        judge.evaluate(
            EvaluatorContext(
                input_data={"question": "What?", "tool": {"name": "search"}},
                output_data="Answer",
            )
        )

        assert "What?" in captured["prompt"]
        assert "Answer" in captured["prompt"]
        assert "search" in captured["prompt"]

    def test_invalid_json_raises(self):
        def mock_client(messages, json_schema=None):
            return "Not JSON"

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanOutput("Check"),
        )
        with pytest.raises(ValueError, match="Invalid JSON"):
            judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

    def test_wrong_type_raises(self):
        def mock_client(messages, json_schema=None):
            return json.dumps({"wrong_field": "not a bool"})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanOutput("Check"),
        )
        with pytest.raises(ValueError, match="Expected boolean"):
            judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))

    def test_requires_client_or_provider(self):
        with pytest.raises(ValueError, match="client.*provider"):
            LLMJudge(user_prompt="test")

    def test_optional_fields_not_set(self):
        def mock_client(messages, json_schema=None):
            return json.dumps({"boolean_eval": True, "reasoning": "ignored"})

        judge = LLMJudge(
            client=mock_client,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanOutput("Check"),  # No pass_when, reasoning=False
        )
        result = judge.evaluate(EvaluatorContext(input_data={}, output_data="test"))
        assert result.assessment is None
        assert result.reasoning is None


class TestLLMJudgePublish:
    def test_publish_requires_llmobs_enabled(self, monkeypatch):
        from ddtrace.llmobs import LLMObs

        monkeypatch.setattr(LLMObs, "_instance", None)

        def mock_client(messages, json_schema=None):
            return "ok"

        judge = LLMJudge(client=mock_client, user_prompt="test")
        with pytest.raises(RuntimeError, match="LLMObs must be enabled"):
            judge.publish("test-app")

    def test_publish_builds_correct_payload(self, llmobs, monkeypatch):
        captured = {}

        def mock_publish(body):
            captured["body"] = body

        monkeypatch.setattr(llmobs._instance._dne_client, "evaluator_config_publish", mock_publish)

        def mock_client(messages, json_schema=None):
            return "{}"

        judge = LLMJudge(
            client=mock_client,
            system_prompt="You are an evaluator.",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanOutput("Correctness", reasoning=True, pass_when=True),
            name="my_evaluator",
        )
        judge._provider = "openai"
        judge._model = "gpt-4o"
        judge.publish("test-app")

        body = captured["body"]
        assert body["data"]["type"] == "evaluator_config"
        evaluation = body["data"]["attributes"]["evaluation"]
        assert evaluation["eval_name"] == "my_evaluator"
        app_config = evaluation["applications"][0]
        assert app_config["application_name"] == "test-app"
        assert app_config["enabled"] is False
        assert app_config["model_provider"] == "openai"
        assert app_config["model_name"] == "gpt-4o"

        byop = app_config["byop_config"]
        assert len(byop["prompt_template"]) == 2
        assert byop["prompt_template"][0]["role"] == "system"
        assert byop["prompt_template"][1]["role"] == "user"
        assert byop["parsing_type"] == "structured_output"
        assert byop["output_schema"]["properties"]["boolean_eval"]["type"] == "boolean"
        assert byop["assessment_criteria"] == {"pass_when": True}
