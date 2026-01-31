"""LLM Judge evaluator for LLM Observability."""

from dataclasses import asdict
from dataclasses import dataclass
import json
import os
import re
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs.types import JSONType


LLMClient = Callable[..., str]


@dataclass
class BooleanOutput:
    """Boolean output with field name "boolean_eval"."""

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
    """Numeric score output with field name "score_eval"."""

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
    """Categorical output with field name "categorical_eval"."""

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


StructuredOutput = Union[BooleanOutput, ScoreOutput, CategoricalOutput, JSONType]


def _create_openai_client(model: str) -> LLMClient:
    """Create OpenAI client from OPENAI_API_KEY env var."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set")
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package required: pip install openai")

    client = OpenAI(api_key=api_key)

    def call(messages: List[Dict[str, str]], json_schema: Optional[Dict[str, Any]] = None) -> str:
        kwargs: Dict[str, Any] = {"model": model, "messages": messages}
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "evaluation", "strict": True, "schema": json_schema},
            }
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    return call


def _create_anthropic_client(model: str) -> LLMClient:
    """Create Anthropic client from ANTHROPIC_API_KEY env var."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package required: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)

    def call(messages: List[Dict[str, str]], json_schema: Optional[Dict[str, Any]] = None) -> str:
        system = None
        user_msgs = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                user_msgs.append(msg)

        kwargs: Dict[str, Any] = {"model": model, "max_tokens": 4096, "messages": user_msgs}
        if system:
            kwargs["system"] = system
        if json_schema:
            schema_copy = json.loads(json.dumps(json_schema))
            for prop_val in schema_copy.get("properties", {}).values():
                if isinstance(prop_val, dict) and prop_val.get("type") == "number":
                    min_val = prop_val.pop("minimum", None)
                    max_val = prop_val.pop("maximum", None)
                    if min_val is not None or max_val is not None:
                        range_str = f" (range: {min_val} to {max_val})"
                        prop_val["description"] = prop_val.get("description", "") + range_str
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema_copy}}

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
    """Evaluator that uses an LLM to judge task outputs.

    Supports structured outputs (boolean, score, categorical) with optional assessment criteria.
    Uses {{field.path}} template syntax for prompt variables.

    Example::

        judge = LLMJudge(
            provider="openai",
            model="gpt-4o",
            user_prompt="Is this answer correct? {{output_data}}",
            structured_output=BooleanOutput("Correctness", reasoning=True, pass_when=True),
        )
    """

    def __init__(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        structured_output: Optional[StructuredOutput] = None,
        provider: Optional[Literal["openai", "anthropic"]] = None,
        model: Optional[str] = None,
        client: Optional[LLMClient] = None,
        name: Optional[str] = None,
    ):
        super().__init__(name=name)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._structured_output = structured_output
        self._provider = provider
        self._model = model

        if client:
            self._client = client
        elif provider == "openai":
            self._model = model or "gpt-4o"
            self._client = _create_openai_client(self._model)
        elif provider == "anthropic":
            self._model = model or "claude-sonnet-4-5-20250929"
            self._client = _create_anthropic_client(self._model)
        else:
            raise ValueError("Provide either 'client' or 'provider' (openai/anthropic)")

    def evaluate(self, context: EvaluatorContext) -> Union[EvaluatorResult, str, Any]:
        user_prompt = self._render(self._user_prompt, context)
        system_prompt = self._system_prompt

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        json_schema = None
        if self._structured_output and self._structured_output is not JSONType:
            json_schema = self._structured_output.to_json_schema()

        response = self._client(messages, json_schema)

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
        json_str = self._extract_json(response)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}") from e

        structured_output = self._structured_output

        if structured_output is JSONType:
            return data

        label = structured_output.label
        result = data.get(label, data) if isinstance(data, dict) else data

        if isinstance(structured_output, BooleanOutput):
            if not isinstance(result, bool):
                raise ValueError(f"Expected boolean, got {type(result).__name__}")
            score = 1.0 if result else 0.0
        elif isinstance(structured_output, ScoreOutput):
            if not isinstance(result, (int, float)):
                raise ValueError(f"Expected number, got {type(result).__name__}")
            score = float(result)
        elif isinstance(structured_output, CategoricalOutput):
            if not isinstance(result, str):
                raise ValueError(f"Expected string, got {type(result).__name__}")
            score = 0.5
        else:
            score = 0.0

        assessment = self._compute_assessment(result, score)
        reasoning = data.get("reasoning") if isinstance(data, dict) and structured_output.reasoning else None

        return EvaluatorResult(
            value=result,
            reasoning=reasoning,
            assessment=assessment,
            metadata={"score": score, "raw_response": data},
        )

    def _compute_assessment(self, result: Any, score: float) -> Optional[str]:
        structured_output = self._structured_output

        if isinstance(structured_output, BooleanOutput) and structured_output.pass_when is not None:
            return "pass" if result == structured_output.pass_when else "fail"

        if isinstance(structured_output, CategoricalOutput) and structured_output.pass_values is not None:
            return "pass" if result in structured_output.pass_values else "fail"

        if isinstance(structured_output, ScoreOutput):
            min_t, max_t = structured_output.min_threshold, structured_output.max_threshold
            if min_t is not None and max_t is not None:
                if max_t >= min_t:
                    return "pass" if min_t <= score <= max_t else "fail"
                return "pass" if score < max_t or score > min_t else "fail"
            if min_t is not None:
                return "pass" if score >= min_t else "fail"
            if max_t is not None:
                return "pass" if score <= max_t else "fail"

        return None

    @staticmethod
    def _extract_json(text: str) -> str:
        match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
        if match:
            return match.group(1).strip()
        text = text.strip()
        if text.startswith("{") or text.startswith("["):
            return text
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return match.group(0)
        return text

    def publish(self, ml_app: str) -> None:
        """Publish this evaluator configuration to Datadog.

        Args:
            ml_app: The ML application name to associate with this evaluator.
        """
        from ddtrace.llmobs import LLMObs

        if not LLMObs._instance:
            raise RuntimeError("LLMObs must be enabled before publishing evaluator config")

        prompt_template: List[Dict[str, str]] = []
        if self._system_prompt:
            prompt_template.append({"role": "system", "content": self._system_prompt})
        prompt_template.append({"role": "user", "content": self._user_prompt})

        output_schema = None
        assessment_criteria = None
        if self._structured_output and self._structured_output is not JSONType:
            output_schema = self._structured_output.to_json_schema()
            assessment_criteria = self._build_assessment_criteria()

        byop_config: Dict[str, Any] = {
            "inference_params": {},
            "prompt_template": prompt_template,
            "parsing_type": "structured_output",
        }
        if output_schema:
            byop_config["output_schema"] = output_schema
        if assessment_criteria:
            byop_config["assessment_criteria"] = assessment_criteria

        body: Dict[str, Any] = {
            "data": {
                "type": "evaluator_config",
                "attributes": {
                    "evaluation": {
                        "eval_name": self.name,
                        "applications": [
                            {
                                "application_name": ml_app,
                                "enabled": False,
                                "model_provider": self._provider,
                                "model_name": self._model,
                                "byop_config": byop_config,
                            }
                        ],
                    },
                },
            },
        }

        LLMObs._instance._dne_client.evaluator_config_publish(body)

    def _build_assessment_criteria(self) -> Optional[Dict[str, Any]]:
        """Build assessment criteria from structured output configuration."""
        so = self._structured_output
        if isinstance(so, BooleanOutput) and so.pass_when is not None:
            return {"pass_when": so.pass_when}
        if isinstance(so, CategoricalOutput) and so.pass_values is not None:
            return {"pass_values": so.pass_values}
        if isinstance(so, ScoreOutput):
            criteria: Dict[str, Any] = {}
            if so.min_threshold is not None:
                criteria["min_threshold"] = so.min_threshold
            if so.max_threshold is not None:
                criteria["max_threshold"] = so.max_threshold
            return criteria if criteria else None
        return None
