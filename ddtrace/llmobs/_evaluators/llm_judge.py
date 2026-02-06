from abc import ABC
from abc import abstractmethod
from dataclasses import asdict
from dataclasses import dataclass
import json
import os
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs.types import JSONType


class LLMClient(Protocol):
    def __call__(
        self,
        provider: Optional[str],
        messages: List[Dict[str, str]],
        json_schema: Optional[Dict[str, Any]],
        model: str,
        model_params: Optional[Dict[str, Any]],
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
    def to_json_schema(self) -> Dict[str, Any]:
        """Return the JSON schema for structured output."""

    def _build_schema(self, label_schema: Dict[str, Any]) -> Dict[str, Any]:
        """Build JSON schema with the label property and optional reasoning."""
        properties: Dict[str, Any] = {self.label: label_schema}
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

    def to_json_schema(self) -> Dict[str, Any]:
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

    def to_json_schema(self) -> Dict[str, Any]:
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

    categories: Dict[str, str]
    reasoning: bool = False
    reasoning_description: Optional[str] = None
    pass_values: Optional[List[str]] = None

    @property
    def label(self) -> str:
        return "categorical_eval"

    def to_json_schema(self) -> Dict[str, Any]:
        any_of = [{"const": value, "description": desc} for value, desc in self.categories.items()]
        return self._build_schema({"type": "string", "anyOf": any_of})


StructuredOutput = Union[
    BooleanStructuredOutput, ScoreStructuredOutput, CategoricalStructuredOutput, Dict[str, JSONType]
]


def _create_openai_client(client_options: Optional[Dict[str, Any]] = None) -> LLMClient:
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
        messages: List[Dict[str, str]],
        json_schema: Optional[Dict[str, Any]],
        model: str,
        model_params: Optional[Dict[str, Any]],
    ) -> str:
        kwargs: Dict[str, Any] = {"model": model, "messages": messages}
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


def _create_azure_openai_client(client_options: Optional[Dict[str, Any]] = None) -> LLMClient:
    client_options = client_options or {}
    api_key = client_options.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY")
    azure_endpoint = client_options.get("azure_endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_version = (
        client_options.get("api_version") or os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-02-15-preview"
    )

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

    def call(
        provider: Optional[str],
        messages: List[Dict[str, str]],
        json_schema: Optional[Dict[str, Any]],
        model: str,
        model_params: Optional[Dict[str, Any]],
    ) -> str:
        deployment = client_options.get("azure_deployment") or os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model
        kwargs: Dict[str, Any] = {"model": deployment, "messages": messages}
        if model_params:
            kwargs.update(model_params)
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "evaluation", "strict": True, "schema": json_schema},
            }
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    return call


def _create_anthropic_client(client_options: Optional[Dict[str, Any]] = None) -> LLMClient:
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
        messages: List[Dict[str, str]],
        json_schema: Optional[Dict[str, Any]],
        model: str,
        model_params: Optional[Dict[str, Any]],
    ) -> str:
        system_msgs = []
        user_msgs = []
        for msg in messages:
            if msg["role"] == "system":
                system_msgs.append(msg["content"])
            else:
                user_msgs.append(msg)
        system = "\n".join(system_msgs) if system_msgs else None

        kwargs: Dict[str, Any] = {"model": model, "max_tokens": 4096, "messages": user_msgs}
        if model_params:
            kwargs.update(model_params)
        if system:
            kwargs["system"] = system
        if json_schema:
            schema_copy = json.loads(json.dumps(json_schema))
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


class LLMJudge(BaseEvaluator):
    """Evaluator that uses an LLM to judge LLM Observability span outputs."""

    def __init__(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        structured_output: Optional[StructuredOutput] = None,
        provider: Optional[Literal["openai", "anthropic", "azure_openai"]] = None,
        model: Optional[str] = None,
        model_params: Optional[Dict[str, Any]] = None,
        client: Optional[LLMClient] = None,
        name: Optional[str] = None,
        client_options: Optional[Dict[str, Any]] = None,
    ):
        """Initialize an LLMJudge evaluator.

        LLMJudge enables automated evaluation of LLM outputs using another LLM as the judge.
        It supports multiple providers (OpenAI, Anthropic, Azure OpenAI) and output formats
        for flexible evaluation criteria.

        Supported Output Types:
            - ``BooleanStructuredOutput``: Returns True/False with optional pass/fail assessment.
            - ``ScoreStructuredOutput``: Returns a numeric score within a defined range with optional thresholds.
            - ``CategoricalStructuredOutput``: Returns one of predefined categories with optional pass values.
            - ``Dict[str, JSONType]``: Custom JSON schema for arbitrary structured responses.

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
                ``"azure_openai"``. Required if ``client`` is not provided.
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
                    - ``api_version``: API version (default: "2024-02-15-preview").
                      Falls back to ``AZURE_OPENAI_API_VERSION``.
                    - ``azure_deployment``: Deployment name. Falls back to
                      ``AZURE_OPENAI_DEPLOYMENT`` or uses ``model`` param.

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
        else:
            raise ValueError("Provide either 'client' or 'provider' (openai/anthropic/azure_openai)")

    def evaluate(self, context: EvaluatorContext) -> Union[EvaluatorResult, str, Any]:
        if self._model is None:
            raise ValueError("model must be specified")

        user_prompt = self._render(self._user_prompt, context)
        system_prompt = self._system_prompt

        messages: List[Dict[str, str]] = []
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
