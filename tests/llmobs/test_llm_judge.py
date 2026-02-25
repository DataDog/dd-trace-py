"""Tests for LLMJudge evaluator."""

import json
from unittest import mock

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import _create_azure_openai_client
from ddtrace.llmobs._evaluators.llm_judge import _create_bedrock_client
from ddtrace.llmobs._evaluators.llm_judge import _create_vertexai_client
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
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
    def _mock_publish_backend(monkeypatch):
        mock_response = mock.Mock(status=200, reason="OK", body=b"")
        mock_response.get_json.return_value = {}
        mock_dne_client = mock.Mock()
        mock_dne_client.publish_custom_evaluator.return_value = mock_response
        monkeypatch.setattr(LLMObs, "_instance", mock.Mock(_dne_client=mock_dne_client))
        return mock_dne_client

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
        self, provider, expected_provider, expects_wrapped_schema, monkeypatch
    ):
        mock_dne_client = self._mock_publish_backend(monkeypatch)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider=provider,
            user_prompt="Evaluate: {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            model_params={"temperature": 0.2},
            name="quality_eval",
        )

        with mock.patch("ddtrace.llmobs._evaluators.llm_judge._get_base_url", return_value="https://app.datadoghq.com"):
            result = judge.publish(ml_app="test-app")

        assert result["ui_url"] == (
            "https://app.datadoghq.com/llm/evaluations/custom?evalName=quality_eval&applicationName=test-app"
        )

        payload = mock_dne_client.publish_custom_evaluator.call_args.args[0]
        assert payload["eval_name"] == "quality_eval"
        app_payload = payload["applications"][0]

        assert app_payload["application_name"] == "test-app"
        assert app_payload["enabled"] is False
        assert app_payload["integration_provider"] == expected_provider

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

    @pytest.mark.parametrize(
        "model,expected_model_name",
        [
            ("  gpt-4o  ", "gpt-4o"),
            (None, None),
            ("   ", None),
        ],
    )
    def test_publish_sends_model_name_only_when_present(self, model, expected_model_name, monkeypatch):
        mock_dne_client = self._mock_publish_backend(monkeypatch)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            model=model,
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            name="model_eval",
        )

        judge.publish(ml_app="my-app")
        app_payload = mock_dne_client.publish_custom_evaluator.call_args.args[0]["applications"][0]

        assert app_payload["model_provider"] == app_payload["integration_provider"]
        if expected_model_name is None:
            assert "model_name" not in app_payload
        else:
            assert app_payload["model_name"] == expected_model_name

    def test_publish_variable_mapping_replaces_prompt_placeholders(self, monkeypatch):
        mock_dne_client = self._mock_publish_backend(monkeypatch)

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

        judge.publish(
            ml_app="test-app",
            variable_mapping={"input_data": "span_input", "output_data": "span_output"},
        )

        payload = mock_dne_client.publish_custom_evaluator.call_args.args[0]
        prompt_template = payload["applications"][0]["byop_config"]["prompt_template"]

        assert prompt_template[0]["content"] == "System sees {{input_data}}"
        assert prompt_template[1]["content"] == (
            "Input {{span_input}} Output {{span_output}} Expected {{expected_output}} Metadata {{metadata.customer_id}}"
        )

    def test_publish_variable_mapping_does_not_chain_replacements(self, monkeypatch):
        mock_dne_client = self._mock_publish_backend(monkeypatch)

        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Input {{input_data}} Output {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness", pass_when=True),
            name="mapping_eval",
        )

        judge.publish(
            ml_app="test-app",
            variable_mapping={"input_data": "output_data", "output_data": "span_output"},
        )

        payload = mock_dne_client.publish_custom_evaluator.call_args.args[0]
        prompt_template = payload["applications"][0]["byop_config"]["prompt_template"]

        assert prompt_template[1]["content"] == "Input {{output_data}} Output {{span_output}}"

    def test_publish_custom_schema_uses_json_parsing_and_encoded_url(self, monkeypatch):
        mock_dne_client = self._mock_publish_backend(monkeypatch)

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

        with mock.patch("ddtrace.llmobs._evaluators.llm_judge._get_base_url", return_value="https://app.datadoghq.com"):
            result = judge.publish(ml_app="my app")

        assert result["ui_url"] == (
            "https://app.datadoghq.com/llm/evaluations/custom?evalName=json_eval&applicationName=my+app"
        )

        payload = mock_dne_client.publish_custom_evaluator.call_args.args[0]
        app_payload = payload["applications"][0]
        assert app_payload["integration_provider"] == "vertex_ai"
        assert app_payload["byop_config"]["parsing_type"] == "json"
        assert app_payload["byop_config"]["output_schema"] == custom_schema
        assert "assessment_criteria" not in app_payload["byop_config"]

    def test_publish_requires_ml_app(self):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
        )
        with pytest.raises(ValueError, match="ml_app"):
            judge.publish(ml_app="   ")

    def test_publish_requires_structured_output(self):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=None,
        )
        with pytest.raises(ValueError, match="structured_output"):
            judge.publish(ml_app="my-app")

    def test_publish_validates_eval_name_format(self, monkeypatch):
        self._mock_publish_backend(monkeypatch)
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
            name="valid_name",
        )
        with pytest.raises(ValueError, match="Evaluator name .* is invalid"):
            judge.publish(ml_app="my-app", eval_name="invalid name!")

    def test_publish_accepts_hyphenated_eval_name(self, monkeypatch):
        mock_dne_client = self._mock_publish_backend(monkeypatch)
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
            name="fallback",
        )
        judge.publish(ml_app="my-app", eval_name="hyphen-name")
        payload = mock_dne_client.publish_custom_evaluator.call_args.args[0]
        assert payload["eval_name"] == "hyphen-name"

    def test_publish_validates_variable_mapping(self):
        judge = LLMJudge(
            client=lambda *args, **kwargs: "",
            provider="openai",
            user_prompt="Evaluate {{output_data}}",
            structured_output=BooleanStructuredOutput("Correctness"),
        )
        with pytest.raises(ValueError, match="variable_mapping keys"):
            judge.publish(ml_app="my-app", variable_mapping={"": "span_output"})

        with pytest.raises(ValueError, match="variable_mapping values"):
            judge.publish(ml_app="my-app", variable_mapping={"output_data": "   "})


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
