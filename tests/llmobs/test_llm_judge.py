"""Tests for LLMJudge evaluator."""

import json
from unittest import mock

import pytest

from ddtrace.llmobs._constants import EVAL_NAME_TAG
from ddtrace.llmobs._constants import EVAL_SOURCE_TYPE_TAG
from ddtrace.llmobs._constants import EVALUATED_EXPERIMENT_ID_TAG
from ddtrace.llmobs._constants import EVALUATED_ML_APP_TAG
from ddtrace.llmobs._constants import EVALUATED_SPAN_ID_TAG
from ddtrace.llmobs._constants import EVALUATED_TRACE_ID_TAG
from ddtrace.llmobs._constants import EVALUATIONS_ML_APP
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import _create_azure_openai_client
from ddtrace.llmobs._evaluators.llm_judge import _create_bedrock_client
from ddtrace.llmobs._evaluators.llm_judge import _create_vertexai_client
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_ml_app
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import get_llmobs_trace_id
from tests.llmobs._utils import get_azure_openai_vcr
from tests.llmobs._utils import get_bedrock_vcr
from tests.llmobs._utils import get_vertexai_vcr


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


class TestLLMJudgePublish:
    @staticmethod
    def _mock_publish_backend(monkeypatch, llmobs):
        mock_publish = mock.Mock(return_value=mock.Mock(status=200))
        monkeypatch.setattr(llmobs._instance._dne_client, "publish_custom_evaluator", mock_publish)
        return mock_publish

    @pytest.mark.parametrize(
        "provider,expected_provider,expects_wrapped_schema",
        [
            ("openai", "openai", True),
            ("azure_openai", "azure_openai", True),
            ("anthropic", "anthropic", False),
            ("vertexai", "vertex_ai", False),
            ("bedrock", "amazon_bedrock", False),
        ],
    )
    def test_publish_provider_mapping_and_schema_format(
        self, provider, expected_provider, expects_wrapped_schema, monkeypatch, llmobs
    ):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider=provider,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            model_params={"temperature": 0.2},
            name="quality_eval",
        )

        with mock.patch("ddtrace.llmobs._llmobs._get_base_url", return_value="https://app.datadoghq.com"):
            result = llmobs.publish_evaluator(judge, ml_app="test-app")

        assert result["ui_url"] == (
            "https://app.datadoghq.com/llm/evaluations/custom?evalName=quality_eval&applicationName=test-app"
        )

        payload = mock_publish.call_args.args[0]
        assert payload["eval_name"] == "quality_eval"
        app_payload = payload["applications"][0]

        assert app_payload["application_name"] == "test-app"
        assert app_payload["enabled"] is False
        assert app_payload["integration_provider"] == expected_provider
        assert app_payload["model_provider"] == expected_provider
        assert set(app_payload) == {
            "application_name",
            "enabled",
            "integration_provider",
            "model_provider",
            "byop_config",
        }

        byop_config = app_payload["byop_config"]
        assert byop_config["inference_params"] == {"temperature": 0.2}
        assert byop_config["parsing_type"] == "structured_output"
        assert byop_config["assessment_criteria"] == {"pass_when": True}
        assert byop_config["prompt_template"] == [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Evaluate: {{output_data}}"},
        ]

        output_schema = byop_config["output_schema"]
        if expects_wrapped_schema:
            assert output_schema["name"] == "boolean_eval"
            assert output_schema["strict"] is True
            assert output_schema["schema"]["properties"]["boolean_eval"]["type"] == "boolean"
        else:
            assert output_schema["properties"]["boolean_eval"]["type"] == "boolean"

    def test_publish_agent_service_preferred_name(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            name="quality_eval",
        )

        with mock.patch("ddtrace.llmobs._llmobs._get_base_url", return_value="https://app.datadoghq.com"):
            result = llmobs.publish_evaluator(judge, ml_app="legacy-ml-app", agent_service="test-agent-service")

        assert result["ui_url"] == (
            "https://app.datadoghq.com/llm/evaluations/custom?evalName=quality_eval&applicationName=test-agent-service"
        )
        payload = mock_publish.call_args.args[0]
        assert payload["applications"][0]["application_name"] == "test-agent-service"

    def test_publish_score_output_includes_threshold_assessment_criteria(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Score: {{output_data}}",
            structured_output=ScoreStructuredOutput(
                "Quality",
                min_score=0.0,
                max_score=1.0,
                min_threshold=0.3,
                max_threshold=0.8,
            ),
            name="score_eval_publish",
        )

        llmobs.publish_evaluator(judge, ml_app="test-app")
        payload = mock_publish.call_args.args[0]
        byop_config = payload["applications"][0]["byop_config"]

        assert byop_config["parsing_type"] == "structured_output"
        assert byop_config["assessment_criteria"] == {"min_threshold": 0.3, "max_threshold": 0.8}

    def test_publish_categorical_output_includes_pass_values_assessment_criteria(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Classify: {{output_data}}",
            structured_output=CategoricalStructuredOutput(
                categories={
                    "positive": "Positive sentiment",
                    "negative": "Negative sentiment",
                },
                pass_values=["positive"],
            ),
            name="categorical_eval_publish",
        )

        llmobs.publish_evaluator(judge, ml_app="test-app")
        payload = mock_publish.call_args.args[0]
        byop_config = payload["applications"][0]["byop_config"]

        assert byop_config["parsing_type"] == "structured_output"
        assert byop_config["assessment_criteria"] == {"pass_values": ["positive"]}

    @pytest.mark.parametrize(
        "model,expected_model_name",
        [
            ("  gpt-4o  ", "gpt-4o"),
            (None, None),
            ("   ", None),
        ],
    )
    def test_publish_sends_model_name_only_when_present(self, model, expected_model_name, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            model=model,
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            name="model_eval",
        )

        llmobs.publish_evaluator(judge, ml_app="my-app")
        app_payload = mock_publish.call_args.args[0]["applications"][0]

        assert app_payload["model_provider"] == app_payload["integration_provider"]
        if expected_model_name is None:
            assert "model_name" not in app_payload
        else:
            assert app_payload["model_name"] == expected_model_name

    def test_publish_variable_mapping_replaces_prompt_placeholders(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            system_prompt="System sees {{input_data}}",
            user_prompt=(
                "Input {{input_data}} Output {{output_data}} Expected {{expected_output}} "
                "Metadata {{metadata.customer_id}}"
            ),
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            name="mapping_eval",
        )

        llmobs.publish_evaluator(
            judge,
            ml_app="test-app",
            variable_mapping={"input_data": "span_input", "output_data": "span_output"},
        )

        payload = mock_publish.call_args.args[0]
        prompt_template = payload["applications"][0]["byop_config"]["prompt_template"]

        assert prompt_template[0]["content"] == "System sees {{input_data}}"
        assert prompt_template[1]["content"] == (
            "Input {{span_input}} Output {{span_output}} Expected {{expected_output}} Metadata {{metadata.customer_id}}"
        )

    def test_publish_variable_mapping_does_not_chain_replacements(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Input {{input_data}} Output {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            name="mapping_eval",
        )

        llmobs.publish_evaluator(
            judge,
            ml_app="test-app",
            variable_mapping={"input_data": "output_data", "output_data": "span_output"},
        )

        payload = mock_publish.call_args.args[0]
        prompt_template = payload["applications"][0]["byop_config"]["prompt_template"]

        assert prompt_template[1]["content"] == "Input {{output_data}} Output {{span_output}}"

    def test_publish_custom_schema_uses_json_parsing_and_encoded_url(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)

        custom_schema = {
            "type": "object",
            "properties": {
                "grade": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["grade"],
            "additionalProperties": False,
        }

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="vertexai",
            user_prompt="Grade {{output_data}}",
            structured_output=custom_schema,
            name="json_eval",
        )

        with mock.patch("ddtrace.llmobs._llmobs._get_base_url", return_value="https://app.datadoghq.com"):
            result = llmobs.publish_evaluator(judge, ml_app="my app")

        assert result["ui_url"] == (
            "https://app.datadoghq.com/llm/evaluations/custom?evalName=json_eval&applicationName=my+app"
        )

        payload = mock_publish.call_args.args[0]
        app_payload = payload["applications"][0]
        assert app_payload["integration_provider"] == "vertex_ai"
        assert app_payload["byop_config"]["parsing_type"] == "json"
        assert app_payload["byop_config"]["output_schema"] == custom_schema
        assert "assessment_criteria" not in app_payload["byop_config"]

    def test_publish_requires_ml_app(self, llmobs):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
        )
        with pytest.raises(ValueError, match="agent_service"):
            llmobs.publish_evaluator(judge, ml_app="   ")

    def test_publish_requires_explicit_agent_service_or_ml_app(self, llmobs):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
        )
        with pytest.raises(ValueError, match="agent_service"):
            llmobs.publish_evaluator(judge)

    def test_publish_requires_structured_output(self, llmobs):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=None,
        )
        with pytest.raises(ValueError, match="structured_output"):
            llmobs.publish_evaluator(judge, ml_app="my-app")

    def test_publish_requires_llmobs_enabled(self, monkeypatch, llmobs):
        monkeypatch.setattr(llmobs, "enabled", False)
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
        )
        with pytest.raises(ValueError, match="LLMObs is not enabled"):
            llmobs.publish_evaluator(judge, ml_app="my-app")

    def test_publish_validates_eval_name_format(self, monkeypatch, llmobs):
        self._mock_publish_backend(monkeypatch, llmobs)
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
            name="valid_name",
        )
        with pytest.raises(ValueError, match="Evaluator name .* is invalid"):
            llmobs.publish_evaluator(judge, ml_app="my-app", eval_name="invalid name!")

    def test_publish_accepts_hyphenated_eval_name(self, monkeypatch, llmobs):
        mock_publish = self._mock_publish_backend(monkeypatch, llmobs)
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
            name="fallback",
        )
        llmobs.publish_evaluator(judge, ml_app="my-app", eval_name="hyphen-name")
        payload = mock_publish.call_args.args[0]
        assert payload["eval_name"] == "hyphen-name"

    def test_publish_validates_variable_mapping(self, llmobs):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
        )
        with pytest.raises(ValueError, match="variable_mapping keys"):
            llmobs.publish_evaluator(judge, ml_app="my-app", variable_mapping={"": "span_output"})

        with pytest.raises(ValueError, match="variable_mapping values"):
            llmobs.publish_evaluator(judge, ml_app="my-app", variable_mapping={"output_data": "   "})


AZURE_OPENAI_CLIENT_OPTIONS = {
    "api_key": "testing",
    "azure_endpoint": "https://test.openai.azure.com",
    "api_version": "2024-10-21",
    "azure_deployment": "gpt-4o",
}

VERTEXAI_CLIENT_OPTIONS = {
    "project": "test-project",
    "location": "us-central1",
    "credentials": mock.MagicMock(),
}


@pytest.fixture(scope="session")
def azure_openai_vcr():
    yield get_azure_openai_vcr()


@pytest.fixture(scope="session")
def vertexai_vcr():
    yield get_vertexai_vcr()


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


class TestVertexAIClient:
    def test_missing_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        with mock.patch("google.auth.default", side_effect=Exception("no credentials")):
            with pytest.raises(ValueError, match="Google Cloud credentials not provided"):
                _create_vertexai_client()

    def test_project_from_default_credentials(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        mock_credentials = mock.MagicMock()
        with (
            mock.patch("google.auth.default", return_value=(mock_credentials, "adc-project")),
            mock.patch("vertexai.init") as mock_init,
        ):
            _create_vertexai_client()
            mock_init.assert_called_once_with(
                project="adc-project", location="us-central1", credentials=mock_credentials
            )

    def test_explicit_project_overrides_adc(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        mock_credentials = mock.MagicMock()
        with (
            mock.patch("google.auth.default", return_value=(mock_credentials, "adc-project")),
            mock.patch("vertexai.init") as mock_init,
        ):
            _create_vertexai_client()
            mock_init.assert_called_once_with(
                project="env-project", location="us-central1", credentials=mock_credentials
            )

    @staticmethod
    def _patch_vertexai_init_rest():
        """Patch vertexai.init to force REST transport so VCR can intercept HTTP calls."""
        import vertexai

        original_init = vertexai.init

        def patched_init(**kwargs):
            kwargs["api_transport"] = "rest"
            return original_init(**kwargs)

        return mock.patch("vertexai.init", side_effect=patched_init)

    def test_client_call(self, vertexai_vcr):
        with self._patch_vertexai_init_rest(), vertexai_vcr.use_cassette("vertexai_generate_content_boolean.yaml"):
            client = _create_vertexai_client(VERTEXAI_CLIENT_OPTIONS)
            result = client(
                provider="vertexai",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={
                    "type": "object",
                    "properties": {"boolean_eval": {"type": "boolean"}},
                    "required": ["boolean_eval"],
                    "additionalProperties": False,
                },
                model="gemini-1.5-pro",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )
        assert result == '{"boolean_eval": true}'

    def test_client_call_with_score_schema(self, vertexai_vcr):
        with self._patch_vertexai_init_rest(), vertexai_vcr.use_cassette("vertexai_generate_content_score.yaml"):
            client = _create_vertexai_client(VERTEXAI_CLIENT_OPTIONS)
            result = client(
                provider="vertexai",
                messages=[{"role": "system", "content": "Judge"}, {"role": "user", "content": "test"}],
                json_schema={
                    "type": "object",
                    "properties": {
                        "score_eval": {"type": "number", "minimum": 1, "maximum": 10, "description": "Score"},
                    },
                    "required": ["score_eval"],
                    "additionalProperties": False,
                },
                model="gemini-1.5-pro",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )
        parsed = json.loads(result)
        assert parsed["score_eval"] == 8

    def test_client_call_with_categorical_schema(self, vertexai_vcr):
        with self._patch_vertexai_init_rest(), vertexai_vcr.use_cassette("vertexai_generate_content_categorical.yaml"):
            client = _create_vertexai_client(VERTEXAI_CLIENT_OPTIONS)
            result = client(
                provider="vertexai",
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
                model="gemini-1.5-pro",
                model_params={"temperature": 0.5, "max_tokens": 1024},
            )
        parsed = json.loads(result)
        assert parsed["categorical_eval"] == "positive"


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


class TestClientOptionsPassthrough:
    """Tests that extra client_options are forwarded to underlying client constructors."""

    def test_openai_extra_options(self):
        mock_openai_mod = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"openai": mock_openai_mod}):
            from ddtrace.llmobs._evaluators import llm_judge as lj

            lj._create_openai_client(client_options={"api_key": "test-key", "base_url": "https://custom.endpoint/v1"})
            mock_openai_mod.OpenAI.assert_called_once_with(api_key="test-key", base_url="https://custom.endpoint/v1")

    def test_anthropic_extra_options(self):
        mock_anthropic_mod = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
            from ddtrace.llmobs._evaluators import llm_judge as lj

            lj._create_anthropic_client(
                client_options={"api_key": "test-key", "base_url": "https://custom.endpoint", "max_retries": 5}
            )
            mock_anthropic_mod.Anthropic.assert_called_once_with(
                api_key="test-key", base_url="https://custom.endpoint", max_retries=5
            )

    def test_azure_openai_extra_options(self):
        mock_openai_mod = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"openai": mock_openai_mod}):
            from ddtrace.llmobs._evaluators import llm_judge as lj

            lj._create_azure_openai_client(
                client_options={
                    "api_key": "test-key",
                    "azure_endpoint": "https://test.openai.azure.com",
                    "api_version": "2024-10-21",
                    "azure_deployment": "my-deploy",
                    "timeout": 30,
                }
            )
            mock_openai_mod.AzureOpenAI.assert_called_once_with(
                api_key="test-key",
                azure_endpoint="https://test.openai.azure.com",
                api_version="2024-10-21",
                timeout=30,
            )

    def test_bedrock_extra_options(self):
        mock_boto3 = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"boto3": mock_boto3}):
            from ddtrace.llmobs._evaluators import llm_judge as lj

            lj._create_bedrock_client(
                client_options={
                    "aws_access_key_id": "key",
                    "aws_secret_access_key": "secret",
                    "region_name": "us-west-2",
                    "botocore_session": "custom-session",
                }
            )
            mock_boto3.Session.assert_called_once_with(
                region_name="us-west-2",
                aws_access_key_id="key",
                aws_secret_access_key="secret",
                botocore_session="custom-session",
            )

    def test_vertexai_extra_options(self):
        mock_vertexai = mock.MagicMock()
        mock_creds = mock.MagicMock()
        with mock.patch.dict(
            "sys.modules",
            {
                "vertexai": mock_vertexai,
                "vertexai.generative_models": mock_vertexai.generative_models,
            },
        ):
            from ddtrace.llmobs._evaluators import llm_judge as lj

            lj._create_vertexai_client(
                client_options={
                    "credentials": mock_creds,
                    "project": "my-project",
                    "location": "europe-west1",
                    "api_transport": "rest",
                }
            )
            mock_vertexai.init.assert_called_once_with(
                project="my-project",
                location="europe-west1",
                credentials=mock_creds,
                api_transport="rest",
            )


def _score_judge(client, *, emit_judge_trace, name="relevance"):
    return LLMJudge(
        client=client,
        model="test-model",
        name=name,
        user_prompt="Rate: {{output_data}}",
        structured_output=ScoreStructuredOutput("quality", min_score=1, max_score=10, min_threshold=7),
        emit_judge_trace=emit_judge_trace,
    )


def _ok_client(provider, messages, json_schema, model, model_params):
    return json.dumps({"score_eval": 8})


def _anchored_context(llmobs):
    """An EvaluatorContext anchored to a real evaluated span, as the experiments SDK builds it."""
    with llmobs._experiment(name="task", experiment_id="exp-1", run_id="run-1") as span:
        ref = llmobs.export_span(span)
    return EvaluatorContext(
        input_data={"q": "hi"},
        output_data="hello",
        span_id=ref["span_id"],
        trace_id=ref["trace_id"],
        evaluated_ml_app="my-app",
        evaluated_experiment_id="exp-1",
    )


class TestLLMJudgeJudgeTrace:
    """`emit_judge_trace=True` runs the judge inside an LLMObs.evaluation() judge trace."""

    @staticmethod
    def _capturing_client(seen):
        """A judge client that records the judge span active during inference."""

        def client(provider, messages, json_schema, model, model_params):
            from ddtrace.llmobs import LLMObs

            seen["span"] = LLMObs._instance._current_span()
            return json.dumps({"score_eval": 8})

        return client

    def test_judge_trace_disabled_by_default(self, llmobs):
        result = _score_judge(_ok_client, emit_judge_trace=False).evaluate(_anchored_context(llmobs))
        assert result.value == 8
        assert result.judge_span is None

    def test_judge_trace_attaches_judge_span_to_result(self, llmobs):
        result = _score_judge(_ok_client, emit_judge_trace=True).evaluate(_anchored_context(llmobs))
        assert result.value == 8
        assert isinstance(result.judge_span, dict)
        assert isinstance(result.judge_span["span_id"], str)
        assert isinstance(result.judge_span["trace_id"], str)

    def test_judge_span_carries_eval_contract(self, llmobs):
        """The judge span must satisfy the canonical judge-span contract the UI queries on."""
        seen = {}
        _score_judge(self._capturing_client(seen), emit_judge_trace=True).evaluate(_anchored_context(llmobs))

        judge = seen["span"]
        assert judge is not None
        assert judge.name == "custom_evaluator.relevance"
        # The judge trace is reported under the evaluated application, and EVALUATED_ML_APP_TAG (not
        # the service or a source tag) is what marks it as a judge span.
        assert get_llmobs_ml_app(judge) == "my-app"
        tags = get_llmobs_tags(judge)
        assert tags["source"] != EVALUATIONS_ML_APP
        assert tags[EVAL_NAME_TAG] == "relevance"
        assert tags[EVAL_SOURCE_TYPE_TAG] == "external"
        assert tags[EVALUATED_ML_APP_TAG] == "my-app"

    def test_judge_span_links_back_to_evaluated_span(self, llmobs):
        seen = {}
        ctx = _anchored_context(llmobs)
        _score_judge(self._capturing_client(seen), emit_judge_trace=True).evaluate(ctx)

        tags = get_llmobs_tags(seen["span"])
        assert tags[EVALUATED_SPAN_ID_TAG] == ctx.span_id
        assert tags[EVALUATED_TRACE_ID_TAG] == ctx.trace_id

    def test_judge_span_carries_evaluated_experiment_id(self, llmobs):
        # The judge records the evaluated span's experiment id as a scope hint for the UI back-link.
        # That this does NOT flip the judge into the experiments scope is guarded by
        # test_judge_runs_outside_experiment_scope (same anchored context, asserts SCOPE is None).
        seen = {}
        ctx = _anchored_context(llmobs)
        _score_judge(self._capturing_client(seen), emit_judge_trace=True).evaluate(ctx)
        assert get_llmobs_tags(seen["span"])[EVALUATED_EXPERIMENT_ID_TAG] == ctx.evaluated_experiment_id

    def test_no_evaluated_experiment_id_no_tag(self, llmobs):
        # Non-experiment evaluators (no experiment id on the context) must not carry the tag.
        seen = {}
        ctx = EvaluatorContext(input_data={}, output_data="hi", span_id="1", trace_id="2")
        _score_judge(self._capturing_client(seen), emit_judge_trace=True).evaluate(ctx)
        assert EVALUATED_EXPERIMENT_ID_TAG not in get_llmobs_tags(seen["span"])

    def test_judge_runs_outside_experiment_scope(self, llmobs):
        """Evaluators run after the experiment span closes, so the judge trace must be a
        standalone root trace rather than inheriting the experiment scope.
        """
        seen = {}
        _score_judge(self._capturing_client(seen), emit_judge_trace=True).evaluate(_anchored_context(llmobs))

        judge_data = _get_llmobs_data_metastruct(seen["span"])
        assert judge_data[LLMOBS_STRUCT.PARENT_ID] == "undefined"
        assert judge_data.get(LLMOBS_STRUCT.DD, {}).get(LLMOBS_STRUCT.SCOPE) is None

    def test_nested_judge_spans_join_the_judge_trace(self, llmobs):
        """Auto-instrumented judge LLM calls must land in the judge trace, not the evaluated app's.

        The judge trace now shares the evaluated application's service, so ml_app no longer
        distinguishes the two — assert on the trace the nested span actually joined.
        """
        seen = {}

        def client(provider, messages, json_schema, model, model_params):
            seen["judge"] = llmobs._instance._current_span()
            with llmobs.llm(name="grade", model_name="m", model_provider="p") as llm_span:
                seen["nested"] = llm_span
            return json.dumps({"score_eval": 8})

        _score_judge(client, emit_judge_trace=True).evaluate(_anchored_context(llmobs))

        assert get_llmobs_trace_id(seen["nested"]) == get_llmobs_trace_id(seen["judge"])
        assert get_llmobs_parent_id(seen["nested"]) == str(seen["judge"].span_id)
        assert get_llmobs_ml_app(seen["nested"]) == "my-app"

    def test_provider_errors_still_propagate(self, llmobs):
        """A judge failure must reach the experiments SDK, not be swallowed by tracing."""

        def boom(provider, messages, json_schema, model, model_params):
            raise RuntimeError("provider exploded")

        with pytest.raises(RuntimeError, match="provider exploded"):
            _score_judge(boom, emit_judge_trace=True).evaluate(_anchored_context(llmobs))

    def test_unanchored_context_runs_untraced(self, llmobs):
        """With no evaluated span there is nothing to link to, so the judge runs without a trace."""
        result = _score_judge(_ok_client, emit_judge_trace=True).evaluate(
            EvaluatorContext(input_data={}, output_data="x")
        )
        assert result.value == 8
        assert result.judge_span is None

    def test_llmobs_disabled_still_evaluates(self):
        """Judge tracing is opt-in observability: it must never break evaluation when disabled."""
        result = _score_judge(_ok_client, emit_judge_trace=True).evaluate(
            EvaluatorContext(input_data={}, output_data="x", span_id="1", trace_id="2")
        )
        assert result.value == 8
        assert result.judge_span is None

    def test_annotation_failure_does_not_fail_evaluation(self, llmobs):
        """Instrumentation problems must degrade to an untagged trace, never fail the eval."""
        with mock.patch.object(llmobs, "annotate", side_effect=RuntimeError("annotate blew up")):
            result = _score_judge(_ok_client, emit_judge_trace=True).evaluate(_anchored_context(llmobs))
        assert result.value == 8

    def test_export_failure_yields_no_judge_span(self, llmobs):
        # Build the context first, so the patched export_span only affects the judge's own export
        # (inside evaluate), not the context setup which also calls export_span.
        context = _anchored_context(llmobs)
        with mock.patch.object(llmobs, "export_span", side_effect=RuntimeError("export blew up")):
            result = _score_judge(_ok_client, emit_judge_trace=True).evaluate(context)
        assert result.value == 8
        assert result.judge_span is None
