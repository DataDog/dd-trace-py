"""LLM Judge evaluator for LLM Observability."""

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
from typing import Union

from typing_extensions import Protocol

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


@dataclass
class BooleanOutput:
    """Represents a Boolean LLM Judge structured output."""

    description: str
    reasoning: bool = False
    reasoning_description: Optional[str] = None
    pass_when: Optional[bool] = None

    @property
    def label(self) -> str:
        return "boolean_eval"

    def to_json_schema(self) -> Dict[str, Any]:
        properties: Dict[str, Any] = {self.label: {"type": "boolean", "description": self.description}}
        required = [self.label]
        if self.reasoning:
            properties["reasoning"] = {
                "type": "string",
                "description": self.reasoning_description or "Explanation for the evaluation result",
            }
            required.append("reasoning")
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


@dataclass
class ScoreOutput:
    """Represents a Score LLM Judge structured output."""

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
        properties: Dict[str, Any] = {
            self.label: {
                "type": "number",
                "description": self.description,
                "minimum": self.min_score,
                "maximum": self.max_score,
            }
        }
        required = [self.label]
        if self.reasoning:
            properties["reasoning"] = {
                "type": "string",
                "description": self.reasoning_description or "Explanation for the evaluation result",
            }
            required.append("reasoning")
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


@dataclass
class CategoricalOutput:
    """Represents a Categorical LLM Judge structured output."""

    description: str
    categories: List[str]
    reasoning: bool = False
    reasoning_description: Optional[str] = None
    pass_values: Optional[List[str]] = None

    @property
    def label(self) -> str:
        return "categorical_eval"

    def to_json_schema(self) -> Dict[str, Any]:
        properties: Dict[str, Any] = {
            self.label: {"type": "string", "description": self.description, "enum": self.categories}
        }
        required = [self.label]
        if self.reasoning:
            properties["reasoning"] = {
                "type": "string",
                "description": self.reasoning_description or "Explanation for the evaluation result",
            }
            required.append("reasoning")
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


StructuredOutput = Union[BooleanOutput, ScoreOutput, CategoricalOutput, Dict[str, JSONType]]


def _create_openai_client() -> LLMClient:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")
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
        return response.choices[0].message.content or ""

    return call


def _create_anthropic_client() -> LLMClient:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
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
            # Anthropic doesn't support minimum/maximum for number properties in json schema.
            # The range should be described in the description instead.
            schema_copy = json.loads(json.dumps(json_schema))
            for prop_val in schema_copy.get("properties", {}).values():
                if isinstance(prop_val, dict) and prop_val.get("type") == "number":
                    min_val = prop_val.pop("minimum", None)
                    max_val = prop_val.pop("maximum", None)
                    if min_val is not None or max_val is not None:
                        range_str = f" (range: {min_val} to {max_val})"
                        prop_val["description"] = prop_val.get("description", "") + range_str
            # Use beta API for structured outputs
            kwargs["extra_headers"] = {"anthropic-beta": "structured-outputs-2025-11-13"}
            kwargs["extra_body"] = {"output_format": {"type": "json_schema", "schema": schema_copy}}

        response = client.messages.create(**kwargs)
        if not response.content:
            return ""
        block = response.content[0]
        if hasattr(block, "text"):
            return block.text
        if hasattr(block, "json"):
            return json.dumps(block.json)
        return ""

    return call


class LLMJudge(BaseEvaluator):
    """Evaluator that uses an LLM to judge LLM Observability span outputs."""

    def __init__(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        structured_output: Optional[StructuredOutput] = None,
        provider: Optional[Literal["openai", "anthropic"]] = None,
        model: Optional[str] = None,
        model_params: Optional[Dict[str, Any]] = None,
        client: Optional[LLMClient] = None,
        name: Optional[str] = None,
    ):
        """Initialize an LLMJudge evaluator.

        LLMJudge enables automated evaluation of LLM outputs using another LLM as the judge.
        It supports multiple providers (OpenAI, Anthropic) and output formats for flexible
        evaluation criteria.

        Supported Output Types:
            - ``BooleanOutput``: Returns True/False with optional pass/fail assessment.
            - ``ScoreOutput``: Returns a numeric score within a defined range with optional thresholds.
            - ``CategoricalOutput``: Returns one of predefined categories with optional pass values.
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
            system_prompt: Optional system prompt to set judge behavior/persona.
            structured_output: Output format specification (BooleanOutput, ScoreOutput,
                CategoricalOutput, or a custom JSON schema dict).
            provider: LLM provider to use (``"openai"`` or ``"anthropic"``). Required if
                ``client`` is not provided.
            model: Model identifier (e.g., ``"gpt-4o"``, ``"claude-sonnet-4-20250514"``).
            model_params: Additional parameters passed to the LLM API (e.g., temperature).
            client: Custom LLM client implementing the ``LLMClient`` protocol. If provided,
                ``provider`` is not required.
            name: Optional evaluator name for identification in results.

        Raises:
            ValueError: If neither ``client`` nor ``provider`` is provided.

        Examples:
            Boolean evaluation with pass/fail assessment::

                judge = LLMJudge(
                    provider="openai",
                    model="gpt-4o",
                    user_prompt="Is this response factually accurate? Response: {{output_data}}",
                    structured_output=BooleanOutput(
                        description="Whether the response is factually accurate",
                        reasoning=True,
                        pass_when=True,
                    ),
                )

            Score-based evaluation with thresholds::

                judge = LLMJudge(
                    provider="anthropic",
                    model="claude-sonnet-4-20250514",
                    user_prompt="Rate the helpfulness of this response (1-10): {{output_data}}",
                    structured_output=ScoreOutput(
                        description="Helpfulness score",
                        min_score=1,
                        max_score=10,
                        min_threshold=7,  # Scores >= 7 pass
                    ),
                )

            Categorical evaluation::

                judge = LLMJudge(
                    provider="openai",
                    model="gpt-4o",
                    user_prompt="Classify the sentiment: {{output_data}}",
                    structured_output=CategoricalOutput(
                        description="Sentiment classification",
                        categories=["positive", "neutral", "negative"],
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
            self._client = _create_openai_client()
        elif provider == "anthropic":
            self._client = _create_anthropic_client()
        else:
            raise ValueError("Provide either 'client' or 'provider' (openai/anthropic)")

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
        try:
            data = json.loads(response)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}") from e

        structured_output = self._structured_output
        if structured_output is None:
            return EvaluatorResult(value=data)

        if isinstance(structured_output, dict):
            return EvaluatorResult(value=data, reasoning=data.get("reasoning"))

        label = structured_output.label
        result = data.get(label, data)

        if isinstance(structured_output, BooleanOutput):
            if not isinstance(result, bool):
                raise ValueError(f"Expected boolean, got {type(result).__name__}")
        elif isinstance(structured_output, ScoreOutput):
            if not isinstance(result, (int, float)):
                raise ValueError(f"Expected number, got {type(result).__name__}")
        elif isinstance(structured_output, CategoricalOutput):
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
        structured_output = self._structured_output

        if isinstance(structured_output, BooleanOutput) and structured_output.pass_when is not None:
            return "pass" if result == structured_output.pass_when else "fail"

        if isinstance(structured_output, CategoricalOutput) and structured_output.pass_values is not None:
            return "pass" if result in structured_output.pass_values else "fail"

        if isinstance(structured_output, ScoreOutput):
            min_t, max_t = structured_output.min_threshold, structured_output.max_threshold
            if min_t is not None and max_t is not None:
                if max_t >= min_t:
                    return "pass" if min_t <= result <= max_t else "fail"
                return "pass" if result < max_t or result > min_t else "fail"
            if min_t is not None:
                return "pass" if result >= min_t else "fail"
            if max_t is not None:
                return "pass" if result <= max_t else "fail"

        return None
