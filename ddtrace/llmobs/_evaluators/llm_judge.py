from abc import ABC
from abc import abstractmethod
import copy
from dataclasses import asdict
from dataclasses import dataclass
import json
import os
import re
from typing import Any
from typing import Literal
from typing import Optional
from typing import Protocol
from typing import Union
import urllib.parse

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import _get_base_url
from ddtrace.llmobs._experiment import _validate_evaluator_name
from ddtrace.llmobs.types import JSONType


class LLMClient(Protocol):
    def __call__(
        self,
        provider: Optional[str],
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        model: str,
        model_params: Optional[dict[str, Any]],
    ) -> str: ...


class BaseStructuredOutput(ABC):
    """Abstract base class for LLM Judge structured outputs."""

    description: str
    reasoning: bool
    reasoning_description: Optional[str]

    @property
    @abstractmethod
    def label(self) -> str:
        """Return the label key for the evaluation result."""

    @abstractmethod
    def to_json_schema(self) -> dict[str, Any]:
        """Return the JSON schema for structured output."""

    def _build_schema(self, label_schema: dict[str, Any]) -> dict[str, Any]:
        """Build JSON schema with the label property and optional reasoning."""
        properties: dict[str, Any] = {self.label: label_schema}
        required = [self.label]
        if self.reasoning:
            properties["reasoning"] = {
                "type": "string",
                "description": self.reasoning_description or "Explanation for the evaluation result",
            }
            required.append("reasoning")
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


@dataclass
class BooleanStructuredOutput(BaseStructuredOutput):
    """Boolean structured output for true/false evaluations.

    Use ``pass_when`` to define the passing condition for assessments.
    """

    description: str
    reasoning: bool = False
    reasoning_description: Optional[str] = None
    pass_when: Optional[bool] = None

    @property
    def label(self) -> str:
        return "boolean_eval"

    def to_json_schema(self) -> dict[str, Any]:
        return self._build_schema({"type": "boolean", "description": self.description})


@dataclass
class ScoreStructuredOutput(BaseStructuredOutput):
    """Numeric score structured output within a defined range.

    Use ``min_threshold`` and/or ``max_threshold`` for pass/fail assessments:
    - Both set with max >= min: inclusive range [min, max]
    - Both set with max < min: exclusive range (outside (max, min) passes)
    - Only one set: simple >= or <= comparison
    """

    description: str
    min_score: float
    max_score: float
    reasoning: bool = False
    reasoning_description: Optional[str] = None
    min_threshold: Optional[float] = None
    max_threshold: Optional[float] = None

    @property
    def label(self) -> str:
        return "score_eval"

    def to_json_schema(self) -> dict[str, Any]:
        return self._build_schema(
            {
                "type": "number",
                "description": self.description,
                "minimum": self.min_score,
                "maximum": self.max_score,
            }
        )


@dataclass
class CategoricalStructuredOutput(BaseStructuredOutput):
    """Categorical structured output selecting from predefined categories.

    Categories are provided as a dict mapping category values to their descriptions.
    Use ``pass_values`` to define which categories count as passing.
    """

    categories: dict[str, str]
    reasoning: bool = False
    reasoning_description: Optional[str] = None
    pass_values: Optional[list[str]] = None

    @property
    def label(self) -> str:
        return "categorical_eval"

    def to_json_schema(self) -> dict[str, Any]:
        any_of = [{"const": value, "description": desc} for value, desc in self.categories.items()]
        return self._build_schema({"type": "string", "anyOf": any_of})


StructuredOutput = Union[
    BooleanStructuredOutput, ScoreStructuredOutput, CategoricalStructuredOutput, dict[str, JSONType]
]


_PUBLISH_PROVIDER_MAPPING = {
    "openai": "openai",
    "anthropic": "anthropic",
    "azure_openai": "azure_openai",
    "vertexai": "vertex_ai",
    "bedrock": "amazon_bedrock",
}


def _create_openai_client(client_options: Optional[dict[str, Any]] = None) -> LLMClient:
    client_options = client_options or {}
    api_key = client_options.get("api_key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenAI API key not provided. Pass 'api_key' in client_options or set OPENAI_API_KEY environment variable"
        )
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package required: pip install openai")

    client = OpenAI(api_key=api_key)

    def call(
        provider: Optional[str],
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        model: str,
        model_params: Optional[dict[str, Any]],
    ) -> str:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if model_params:
            kwargs.update(model_params)
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "evaluation", "strict": True, "schema": json_schema},
            }
        response = client.chat.completions.create(**kwargs)
        choices = getattr(response, "choices", None)
        if choices and isinstance(choices, list):
            message = getattr(choices[0], "message", None)
            return getattr(message, "content", None) or ""
        return ""

    return call


def _create_azure_openai_client(client_options: Optional[dict[str, Any]] = None) -> LLMClient:
    client_options = client_options or {}
    api_key = client_options.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY")
    azure_endpoint = client_options.get("azure_endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_version = client_options.get("api_version") or os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-10-21"

    if not api_key:
        raise ValueError(
            "Azure OpenAI API key not provided. "
            "Pass 'api_key' in client_options or set AZURE_OPENAI_API_KEY environment variable"
        )
    if not azure_endpoint:
        raise ValueError(
            "Azure OpenAI endpoint not provided. "
            "Pass 'azure_endpoint' in client_options or set AZURE_OPENAI_ENDPOINT environment variable"
        )

    try:
        from openai import AzureOpenAI
    except ImportError:
        raise ImportError("openai package required: pip install openai")

    client = AzureOpenAI(api_key=api_key, azure_endpoint=azure_endpoint, api_version=api_version)
    deployment_name = client_options.get("azure_deployment") or os.environ.get("AZURE_OPENAI_DEPLOYMENT")

    def call(
        provider: Optional[str],
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        model: str,
        model_params: Optional[dict[str, Any]],
    ) -> str:
        kwargs: dict[str, Any] = {"model": deployment_name or model, "messages": messages}
        if model_params:
            kwargs.update(model_params)
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "evaluation", "strict": True, "schema": json_schema},
            }
        response = client.chat.completions.create(**kwargs)
        choices = getattr(response, "choices", None)
        if choices and isinstance(choices, list):
            message = getattr(choices[0], "message", None)
            return getattr(message, "content", None) or ""
        return ""

    return call


def _create_anthropic_client(client_options: Optional[dict[str, Any]] = None) -> LLMClient:
    client_options = client_options or {}
    api_key = client_options.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "Anthropic API key not provided. "
            "Pass 'api_key' in client_options or set ANTHROPIC_API_KEY environment variable"
        )
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package required: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)

    def call(
        provider: Optional[str],
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        model: str,
        model_params: Optional[dict[str, Any]],
    ) -> str:
        system_msgs = []
        user_msgs = []
        for msg in messages:
            if msg["role"] == "system":
                system_msgs.append(msg["content"])
            else:
                user_msgs.append(msg)
        system = "\n".join(system_msgs) if system_msgs else None

        kwargs: dict[str, Any] = {"model": model, "max_tokens": 4096, "messages": user_msgs}
        if model_params:
            kwargs.update(model_params)
        if system:
            kwargs["system"] = system
        if json_schema:
            schema_copy = copy.deepcopy(json_schema)
            for prop_val in schema_copy.get("properties", {}).values():
                if not isinstance(prop_val, dict):
                    continue
                # Anthropic doesn't support minimum/maximum for number properties in json schema.
                # The range should be described in the description instead.
                if prop_val.get("type") == "number":
                    min_val = prop_val.pop("minimum", None)
                    max_val = prop_val.pop("maximum", None)
                    if min_val is not None or max_val is not None:
                        range_str = f" (range: {min_val} to {max_val})"
                        prop_val["description"] = prop_val.get("description", "") + range_str
                # Anthropic doesn't support 'type' on properties that use 'anyOf'.
                if "anyOf" in prop_val:
                    prop_val.pop("type", None)
            # Use beta API for structured outputs
            kwargs["extra_headers"] = {"anthropic-beta": "structured-outputs-2025-11-13"}
            kwargs["extra_body"] = {"output_format": {"type": "json_schema", "schema": schema_copy}}

        response = client.messages.create(**kwargs)
        content = getattr(response, "content", None)
        if content and isinstance(content, list):
            block = content[0]
            text = getattr(block, "text", None)
            if text is not None:
                return text
            json_content = getattr(block, "json", None)
            if json_content is not None:
                return json.dumps(json_content)
        return ""

    return call


def _create_vertexai_client(client_options: Optional[dict[str, Any]] = None) -> LLMClient:
    try:
        import vertexai
        from vertexai.generative_models import GenerationConfig
        from vertexai.generative_models import GenerativeModel
    except ImportError:
        raise ImportError("google-cloud-aiplatform package required: pip install google-cloud-aiplatform")

    client_options = client_options or {}

    credentials = client_options.get("credentials")
    project = (
        client_options.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCLOUD_PROJECT")
    )

    if credentials is None:
        try:
            import google.auth

            credentials, default_project = google.auth.default()
            if not project:
                project = default_project
        except Exception:
            raise ValueError(
                "Google Cloud credentials not provided and Application Default Credentials (ADC) "
                "could not be resolved. Pass 'credentials' in client_options or set the "
                "GOOGLE_APPLICATION_CREDENTIALS environment variable."
            )

    if not project:
        raise ValueError(
            "Google Cloud project not provided. "
            "Pass 'project' in client_options or set GOOGLE_CLOUD_PROJECT environment variable."
        )

    location = (
        client_options.get("location")
        or os.environ.get("GOOGLE_CLOUD_REGION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )

    vertexai.init(project=project, location=location, credentials=credentials)

    def call(
        provider: Optional[str],
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        model: str,
        model_params: Optional[dict[str, Any]],
    ) -> str:
        contents = []
        system_msgs = []
        for msg in messages:
            if msg["role"] == "system":
                system_msgs.append(msg["content"])
            else:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        model_instance = GenerativeModel(model, system_instruction="\n".join(system_msgs))

        generation_config_params = model_params.copy() if model_params else {}
        if "max_tokens" in generation_config_params:
            generation_config_params["max_output_tokens"] = generation_config_params.pop("max_tokens")
        if json_schema:
            schema_copy = copy.deepcopy(json_schema)
            for prop_val in schema_copy.get("properties", {}).values():
                if not isinstance(prop_val, dict):
                    continue
                # Vertex AI doesn't support 'const' in anyOf. Convert to enum.
                if "anyOf" in prop_val:
                    enum_values = [item.pop("const") for item in prop_val["anyOf"] if "const" in item]
                    if enum_values:
                        prop_val.pop("anyOf")
                        prop_val["enum"] = enum_values
            generation_config_params["response_mime_type"] = "application/json"
            generation_config_params["response_schema"] = schema_copy

        generation_config = GenerationConfig(**generation_config_params) if generation_config_params else None
        response = model_instance.generate_content(contents, generation_config=generation_config)

        if response.candidates:
            content = getattr(response.candidates[0], "content", None)
            parts = getattr(content, "parts", []) or []
            if parts and getattr(parts[0], "text", None):
                return parts[0].text
        return ""

    return call


def _create_bedrock_client(client_options: Optional[dict[str, Any]] = None) -> LLMClient:
    client_options = client_options or {}
    region_name = (
        client_options.get("region_name")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )

    try:
        import boto3
    except ImportError:
        raise ImportError("boto3 package required: pip install boto3")

    session_kwargs: dict[str, Any] = {"region_name": region_name}
    profile_name = client_options.get("profile_name") or os.environ.get("AWS_PROFILE")
    if profile_name:
        session_kwargs["profile_name"] = profile_name
    aws_access_key_id = client_options.get("aws_access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID")
    if aws_access_key_id:
        session_kwargs["aws_access_key_id"] = aws_access_key_id
    aws_secret_access_key = client_options.get("aws_secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    if aws_secret_access_key:
        session_kwargs["aws_secret_access_key"] = aws_secret_access_key
    aws_session_token = client_options.get("aws_session_token") or os.environ.get("AWS_SESSION_TOKEN")
    if aws_session_token:
        session_kwargs["aws_session_token"] = aws_session_token

    session = boto3.Session(**session_kwargs)
    client = session.client("bedrock-runtime")

    def call(
        provider: Optional[str],
        messages: list[dict[str, str]],
        json_schema: Optional[dict[str, Any]],
        model: str,
        model_params: Optional[dict[str, Any]],
    ) -> str:
        system_msgs = []
        converse_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msgs.append({"text": msg["content"]})
            else:
                role = "user" if msg["role"] == "user" else "assistant"
                converse_messages.append({"role": role, "content": [{"text": msg["content"]}]})

        kwargs: dict[str, Any] = {"modelId": model, "messages": converse_messages}

        if system_msgs:
            kwargs["system"] = system_msgs

        if model_params:
            inference_config: dict[str, Any] = {}
            for key in ["temperature", "topP", "maxTokens", "stopSequences"]:
                if key in model_params:
                    inference_config[key] = model_params[key]
            if "max_tokens" in model_params:
                inference_config["maxTokens"] = model_params["max_tokens"]
            if "top_p" in model_params:
                inference_config["topP"] = model_params["top_p"]
            if inference_config:
                kwargs["inferenceConfig"] = inference_config

        if json_schema:
            # Bedrock doesn't support minimum/maximum for number properties in json schema.
            # The range should be described in the description instead.
            schema_copy = copy.deepcopy(json_schema)
            for prop_val in schema_copy.get("properties", {}).values():
                if not isinstance(prop_val, dict):
                    continue
                # Bedrock doesn't support minimum/maximum for number properties in json schema.
                # The range should be described in the description instead.
                if prop_val.get("type") == "number":
                    min_val = prop_val.pop("minimum", None)
                    max_val = prop_val.pop("maximum", None)
                    if min_val is not None or max_val is not None:
                        range_str = f" (range: {min_val} to {max_val})"
                        prop_val["description"] = prop_val.get("description", "") + range_str
                # Bedrock doesn't support 'type' on properties that use 'anyOf'.
                if "anyOf" in prop_val:
                    prop_val.pop("type", None)
            kwargs["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "name": "evaluation",
                            "schema": json.dumps(schema_copy),
                        },
                    },
                }
            }

        response = client.converse(**kwargs)

        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])

        for block in content_blocks:
            if "text" in block:
                return block["text"]
        return ""

    return call


class LLMJudge(BaseEvaluator):
    """Evaluator that uses an LLM to judge LLM Observability span outputs."""

    def __init__(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        structured_output: Optional[StructuredOutput] = None,
        provider: Optional[Literal["openai", "anthropic", "azure_openai", "vertexai", "bedrock"]] = None,
        model: Optional[str] = None,
        model_params: Optional[dict[str, Any]] = None,
        client: Optional[LLMClient] = None,
        name: Optional[str] = None,
        client_options: Optional[dict[str, Any]] = None,
    ):
        """Initialize an LLMJudge evaluator.

        LLMJudge enables automated evaluation of LLM outputs using another LLM as the judge.
        It supports multiple providers (OpenAI, Anthropic, Azure OpenAI, Vertex AI, Bedrock) and output formats
        for flexible evaluation criteria.

        Supported Output Types:
            - ``BooleanStructuredOutput``: Returns True/False with optional pass/fail assessment.
            - ``ScoreStructuredOutput``: Returns a numeric score within a defined range with optional thresholds.
            - ``CategoricalStructuredOutput``: Returns one of predefined categories with optional pass values.
            - ``dict[str, JSONType]``: Custom JSON schema for arbitrary structured responses.

        Template Variables:
            Prompts support ``{{field.path}}`` syntax to inject context from the evaluated span:
                - ``{{input_data}}``: The span's input data
                - ``{{output_data}}``: The span's output data
                - ``{{expected_output}}``: Expected output for comparison (if available)
                - ``{{metadata.key}}``: Access nested metadata fields

        Args:
            user_prompt: The prompt template sent to the judge LLM. Use ``{{field}}`` syntax
                to inject span context.
            system_prompt: Optional system prompt to set judge behavior/persona. Does not
                support template variables.
            structured_output: Output format specification (BooleanStructuredOutput, ScoreStructuredOutput,
                CategoricalStructuredOutput, or a custom JSON schema dict).
            provider: LLM provider to use. Supported values: ``"openai"``, ``"anthropic"``,
                ``"azure_openai"``, ``"vertexai"``, ``"bedrock"``. Required if ``client`` is not provided.
            model: Model identifier (e.g., ``"gpt-4o"``, ``"claude-sonnet-4-20250514"``).
            model_params: Additional parameters passed to the LLM API (e.g., temperature).
            client: Custom LLM client implementing the ``LLMClient`` protocol. If provided,
                ``provider`` is not required.
            name: Optional evaluator name for identification in results.
            client_options: Provider-specific configuration options. Supported keys vary
                by provider:

                **OpenAI:**
                    - ``api_key``: API key. Falls back to ``OPENAI_API_KEY`` env var.

                **Anthropic:**
                    - ``api_key``: API key. Falls back to ``ANTHROPIC_API_KEY`` env var.

                **Azure OpenAI:**
                    - ``api_key``: API key. Falls back to ``AZURE_OPENAI_API_KEY`` env var.
                    - ``azure_endpoint``: Endpoint URL. Falls back to ``AZURE_OPENAI_ENDPOINT``.
                    - ``api_version``: API version (default: "2024-10-21").
                      Falls back to ``AZURE_OPENAI_API_VERSION``.
                    - ``azure_deployment``: Deployment name. Falls back to
                      ``AZURE_OPENAI_DEPLOYMENT`` or uses ``model`` param.

                **Vertex AI:**
                    - ``project``: Google Cloud project ID. Falls back to
                      ``GOOGLE_CLOUD_PROJECT`` or ``GCLOUD_PROJECT`` env var,
                      or the project inferred from default credentials.
                    - ``location``: Region (default: "us-central1"). Falls back to
                      ``GOOGLE_CLOUD_REGION`` or ``GOOGLE_CLOUD_LOCATION``.
                    - ``credentials``: Optional service account credentials object.
                      Falls back to Application Default Credentials (ADC), which
                      respects the ``GOOGLE_APPLICATION_CREDENTIALS`` env var.

                **Bedrock:**
                    - ``aws_access_key_id``: AWS access key. Falls back to
                      ``AWS_ACCESS_KEY_ID`` env var.
                    - ``aws_secret_access_key``: AWS secret key. Falls back to
                      ``AWS_SECRET_ACCESS_KEY`` env var.
                    - ``aws_session_token``: Session token. Falls back to
                      ``AWS_SESSION_TOKEN`` env var.
                    - ``region_name``: AWS region (default: "us-east-1"). Falls back to
                      ``AWS_REGION`` or ``AWS_DEFAULT_REGION``.
                    - ``profile_name``: AWS profile name. Falls back to ``AWS_PROFILE``.

        Raises:
            ValueError: If neither ``client`` nor ``provider`` is provided.

        Examples:
            Boolean evaluation with pass/fail assessment::

                judge = LLMJudge(
                    provider="openai",
                    model="gpt-5-mini",
                    user_prompt="Is this response factually accurate? Response: {{output_data}}",
                    structured_output=BooleanStructuredOutput(
                        description="Whether the response is factually accurate",
                        reasoning=True,
                        pass_when=True,
                    ),
                )

            Score-based evaluation with thresholds::

                judge = LLMJudge(
                    provider="anthropic",
                    model="claude-haiku-4-5-20250514",
                    user_prompt="Rate the helpfulness of this response (1-10): {{output_data}}",
                    structured_output=ScoreStructuredOutput(
                        description="Helpfulness score",
                        min_score=1,
                        max_score=10,
                        min_threshold=7,  # Scores >= 7 pass
                    ),
                )

            Categorical evaluation::

                judge = LLMJudge(
                    provider="openai",
                    model="gpt-5-mini",
                    user_prompt="Classify the sentiment: {{output_data}}",
                    structured_output=CategoricalStructuredOutput(
                        categories={
                            "positive": "The response has a positive sentiment",
                            "neutral": "The response has a neutral sentiment",
                            "negative": "The response has a negative sentiment",
                        },
                        pass_values=["positive", "neutral"],
                    ),
                )
        """
        super().__init__(name=name)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._structured_output = structured_output
        self._model_params = model_params
        self._provider = provider
        self._model = model

        if client:
            self._client = client
        elif provider == "openai":
            self._client = _create_openai_client(client_options=client_options)
        elif provider == "anthropic":
            self._client = _create_anthropic_client(client_options=client_options)
        elif provider == "azure_openai":
            self._client = _create_azure_openai_client(client_options=client_options)
        elif provider == "vertexai":
            self._client = _create_vertexai_client(client_options=client_options)
        elif provider == "bedrock":
            self._client = _create_bedrock_client(client_options=client_options)
        else:
            raise ValueError("Provide either 'client' or 'provider' (openai/anthropic/azure_openai/vertexai/bedrock)")

    def evaluate(self, context: EvaluatorContext) -> Union[EvaluatorResult, str, Any]:
        if self._model is None:
            raise ValueError("model must be specified")

        user_prompt = self._render(self._user_prompt, context)
        system_prompt = self._system_prompt

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        json_schema = None
        if self._structured_output:
            if isinstance(self._structured_output, dict):
                json_schema = self._structured_output
            else:
                json_schema = self._structured_output.to_json_schema()

        response = self._client(self._provider, messages, json_schema, self._model, self._model_params)

        if self._structured_output:
            return self._parse_response(response)
        return response

    def publish(
        self,
        ml_app: str,
        eval_name: Optional[str] = None,
        variable_mapping: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        if not isinstance(ml_app, str) or not ml_app.strip():
            raise ValueError("ml_app must be a non-empty string")
        ml_app = ml_app.strip()

        resolved_eval_name = eval_name if eval_name is not None else self.name
        if not isinstance(resolved_eval_name, str) or not resolved_eval_name.strip():
            raise ValueError("eval_name must be provided either as argument or evaluator name")
        resolved_eval_name = resolved_eval_name.strip()
        _validate_evaluator_name(resolved_eval_name)

        if self._provider is None:
            raise ValueError("provider must be specified to publish LLMJudge")

        integration_provider = _PUBLISH_PROVIDER_MAPPING.get(self._provider)
        if integration_provider is None:
            raise ValueError(
                "Unsupported provider '{}' for publish(). Expected one of: {}".format(
                    self._provider, ", ".join(sorted(_PUBLISH_PROVIDER_MAPPING))
                )
            )

        normalized_variable_mapping = self._validate_variable_mapping(variable_mapping)
        output_schema, parsing_type, assessment_criteria = self._build_publish_schema_and_criteria(integration_provider)

        prompt_template = [
            {
                "role": "system",
                "content": self._system_prompt or "",
            },
            {
                "role": "user",
                "content": self._apply_variable_mapping(self._user_prompt, normalized_variable_mapping),
            },
        ]

        byop_config: dict[str, Any] = {
            "inference_params": self._model_params or {},
            "prompt_template": prompt_template,
            "output_schema": output_schema,
            "parsing_type": parsing_type,
        }
        if assessment_criteria is not None:
            byop_config["assessment_criteria"] = assessment_criteria

        app_payload: dict[str, Any] = {
            "application_name": ml_app,
            "enabled": False,
            "integration_provider": integration_provider,
            "byop_config": byop_config,
        }
        model_name = self._model.strip() if isinstance(self._model, str) else ""
        if model_name:
            app_payload["model_name"] = model_name

        evaluation_payload: dict[str, Any] = {
            "eval_name": resolved_eval_name,
            "applications": [app_payload],
        }

        from ddtrace.llmobs import LLMObs

        dne_client = getattr(getattr(LLMObs, "_instance", None), "_dne_client", None)
        if dne_client is None:
            raise ValueError(
                "LLMObs experiments client is not initialized. Ensure Datadog API and application keys are configured."
            )

        response = dne_client.publish_custom_evaluator(evaluation_payload)
        if response.status != 200:
            raise ValueError(
                "Failed to publish evaluator {}: {} {}".format(
                    resolved_eval_name, response.status, response.get_json() or response.body
                )
            )

        query = urllib.parse.urlencode({"evalName": resolved_eval_name, "applicationName": ml_app})
        return {"ui_url": "{}{}?{}".format(_get_base_url(), "/llm/evaluations/custom", query)}

    @staticmethod
    def _validate_variable_mapping(variable_mapping: Optional[dict[str, str]]) -> dict[str, str]:
        if variable_mapping is None:
            return {}
        if not isinstance(variable_mapping, dict):
            raise ValueError("variable_mapping must be a dictionary")

        normalized_mapping: dict[str, str] = {}
        for key, value in variable_mapping.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("variable_mapping keys must be non-empty strings")
            if not isinstance(value, str) or not value.strip():
                raise ValueError("variable_mapping values must be non-empty strings")
            normalized_mapping[key.strip()] = value.strip()

        return normalized_mapping

    @staticmethod
    def _apply_variable_mapping(template: str, variable_mapping: dict[str, str]) -> str:
        if not variable_mapping:
            return template

        pattern = re.compile(r"\{\{\s*(" + "|".join(re.escape(key) for key in variable_mapping) + r")\s*\}\}")

        def replace(match: re.Match[str]) -> str:
            return "{{" + variable_mapping[match.group(1)] + "}}"

        return pattern.sub(replace, template)

    def _build_publish_schema_and_criteria(
        self, integration_provider: str
    ) -> tuple[dict[str, Any], str, Optional[dict[str, JSONType]]]:
        structured_output = self._structured_output
        if structured_output is None:
            raise ValueError("structured_output must be provided to publish an evaluator")

        if isinstance(structured_output, dict):
            return (
                self._format_schema_for_provider(structured_output, integration_provider, "evaluation"),
                "json",
                None,
            )

        schema_name = structured_output.label
        schema = structured_output.to_json_schema()
        return (
            self._format_schema_for_provider(schema, integration_provider, schema_name),
            "structured_output",
            self._build_assessment_criteria(structured_output),
        )

    @staticmethod
    def _format_schema_for_provider(
        schema: dict[str, Any], integration_provider: str, schema_name: str
    ) -> dict[str, Any]:
        if integration_provider in {"openai", "azure_openai"}:
            return {"name": schema_name, "strict": True, "schema": schema}
        return schema

    @staticmethod
    def _build_assessment_criteria(structured_output: BaseStructuredOutput) -> Optional[dict[str, JSONType]]:
        criteria: dict[str, Any] = {}

        if isinstance(structured_output, BooleanStructuredOutput) and structured_output.pass_when is not None:
            criteria["pass_when"] = structured_output.pass_when
        elif isinstance(structured_output, ScoreStructuredOutput):
            if structured_output.min_threshold is not None:
                criteria["min_threshold"] = structured_output.min_threshold
            if structured_output.max_threshold is not None:
                criteria["max_threshold"] = structured_output.max_threshold
        elif isinstance(structured_output, CategoricalStructuredOutput) and structured_output.pass_values is not None:
            criteria["pass_values"] = structured_output.pass_values

        return criteria or None

    def _render(self, template: str, context: EvaluatorContext) -> str:
        """Render a prompt template by substituting {{field.path}} placeholders with context values.

        Args:
            template: Prompt template string with {{field.path}} placeholders.
            context: EvaluatorContext containing input_data, output_data, expected_output, and metadata.

        Returns:
            Rendered prompt string with placeholders replaced by actual values.
        """
        ctx = asdict(context)

        def resolve(path: str) -> Any:
            parts = path.split(".")
            value = ctx.get(parts[0])
            for part in parts[1:]:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return None
            return value

        def replace(match: re.Match) -> str:
            value = resolve(match.group(1).strip())
            if value is None:
                return ""
            if isinstance(value, (dict, list)):
                return json.dumps(value, indent=2)
            return str(value)

        return re.sub(r"\{\{(.+?)\}\}", replace, template)

    def _parse_response(self, response: str) -> Union[EvaluatorResult, Any]:
        """Parse the LLM response and extract structured evaluation results.

        Args:
            response: Raw JSON string response from the LLM.

        Returns:
            EvaluatorResult with extracted value, reasoning, and assessment.

        Raises:
            ValueError: If the response is not valid JSON or doesn't match expected schema.
        """
        if not response or not isinstance(response, str):
            raise ValueError("Invalid response: expected non-empty string")
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Invalid JSON response: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"Invalid JSON response: expected object, got {type(data).__name__}")

        structured_output = self._structured_output
        if isinstance(structured_output, dict):
            return EvaluatorResult(value=data, reasoning=data.get("reasoning"))

        if structured_output is None:
            return EvaluatorResult(value=data, reasoning=data.get("reasoning"))

        label = getattr(structured_output, "label", None)
        result = data.get(label) if label else data

        if isinstance(structured_output, BooleanStructuredOutput):
            if not isinstance(result, bool):
                raise ValueError(f"Expected boolean, got {type(result).__name__}")
        elif isinstance(structured_output, ScoreStructuredOutput):
            if not isinstance(result, (int, float)):
                raise ValueError(f"Expected number, got {type(result).__name__}")
        elif isinstance(structured_output, CategoricalStructuredOutput):
            if not isinstance(result, str):
                raise ValueError(f"Expected string, got {type(result).__name__}")

        assessment = self._compute_assessment(result)
        reasoning = data.get("reasoning") if structured_output.reasoning else None

        return EvaluatorResult(
            value=result,
            reasoning=reasoning,
            assessment=assessment,
            metadata={"raw_response": data},
        )

    def _compute_assessment(self, result: Any) -> Optional[str]:
        """Compute pass/fail assessment based on structured output thresholds.

        Args:
            result: The evaluation result value to assess.

        Returns:
            "pass" or "fail" if thresholds are configured, None otherwise.

        Note:
            For ScoreStructuredOutput with both min_threshold and max_threshold:
            - If max >= min: inclusive range, pass if min <= result <= max
            - If max < min: exclusive range, pass if result < max OR result > min
        """
        structured_output = self._structured_output

        if isinstance(structured_output, BooleanStructuredOutput) and structured_output.pass_when is not None:
            return "pass" if result == structured_output.pass_when else "fail"

        if isinstance(structured_output, CategoricalStructuredOutput) and structured_output.pass_values is not None:
            return "pass" if result in structured_output.pass_values else "fail"

        if isinstance(structured_output, ScoreStructuredOutput):
            min_t, max_t = structured_output.min_threshold, structured_output.max_threshold
            if min_t is not None and max_t is not None:
                if max_t >= min_t:
                    # Inclusive range: pass if within [min_t, max_t]
                    return "pass" if min_t <= result <= max_t else "fail"
                # Exclusive range: pass if outside (max_t, min_t)
                return "pass" if result < max_t or result > min_t else "fail"
            if min_t is not None:
                return "pass" if result >= min_t else "fail"
            if max_t is not None:
                return "pass" if result <= max_t else "fail"

        return None
