"""Format validation evaluators for LLMObs."""

import json
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult


class LengthValidator(BaseEvaluator):
    """Evaluator that validates output length constraints.

    Checks if the output_data length falls within specified min/max bounds.
    Useful for ensuring responses meet length requirements, preventing
    overly verbose or too brief outputs.

    Example::

        # Ensure response is between 50-200 characters
        evaluator = LengthValidator(min_length=50, max_length=200, count_type="characters")
        result = evaluator.evaluate(context)
        # Returns: True if within bounds, False otherwise

        # Validate length of extracted field
        evaluator = LengthValidator(
            min_length=10,
            max_length=100,
            output_extractor=lambda x: x.get("summary", "") if isinstance(x, dict) else str(x)
        )
        # Validates the length of the "summary" field

    :param min_length: Minimum allowed length (inclusive), None for no minimum
    :param max_length: Maximum allowed length (inclusive), None for no maximum
    :param count_type: What to count - 'characters', 'words', or 'lines'
    :param output_extractor: Optional function to extract/transform output_data before validation.
                             Must return a str or None. (default: None)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        count_type: str = "characters",
        output_extractor: Optional[Callable[[Any], Optional[str]]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the LengthValidator evaluator.

        :param min_length: Minimum allowed length (None for no minimum)
        :param max_length: Maximum allowed length (None for no maximum)
        :param count_type: One of 'characters', 'words', or 'lines'
        :param output_extractor: Optional function to extract/transform output_data before validation.
                                 Must return a str or None.
        :param name: Optional custom name for the evaluator
        :raises ValueError: If count_type is invalid or min > max
        """
        super().__init__(name=name)

        if count_type not in ("characters", "words", "lines"):
            raise ValueError(f"count_type must be 'characters', 'words', or 'lines', got: {count_type}")

        if min_length is not None and min_length < 0:
            raise ValueError(f"min_length must be non-negative, got: {min_length}")

        if max_length is not None and max_length < 0:
            raise ValueError(f"max_length must be non-negative, got: {max_length}")

        if min_length is not None and max_length is not None and min_length > max_length:
            raise ValueError(f"min_length ({min_length}) cannot be greater than max_length ({max_length})")

        if min_length is None and max_length is None:
            raise ValueError("At least one of min_length or max_length must be specified")

        if output_extractor is not None and not callable(output_extractor):
            raise TypeError("output_extractor must be a callable function")

        self.min_length = min_length
        self.max_length = max_length
        self.count_type = count_type
        self.output_extractor = output_extractor

    def _calculate_length(self, text: str) -> int:
        """Calculate length based on count_type.

        :param text: The text to measure
        :return: The length value
        """
        if self.count_type == "characters":
            return len(text)
        elif self.count_type == "words":
            return len([word for word in text.split() if word])
        else:
            return len(text.splitlines())

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        """Perform length validation.

        :param context: The evaluation context
        :return: EvaluatorResult with boolean value and pass/fail assessment
        """
        output = context.output_data

        if self.output_extractor is not None:
            output = self.output_extractor(output)

        if output is None:
            return EvaluatorResult(value=False, assessment="fail")

        output_str = str(output)
        length = self._calculate_length(output_str)

        if self.min_length is not None and length < self.min_length:
            return EvaluatorResult(value=False, assessment="fail")

        if self.max_length is not None and length > self.max_length:
            return EvaluatorResult(value=False, assessment="fail")

        return EvaluatorResult(value=True, assessment="pass")


class JSONValidator(BaseEvaluator):
    """Evaluator that validates if output is valid JSON.

    Checks if the output_data can be parsed as valid JSON.
    Optionally validates against a specific schema structure.

    Example::

        # Just validate JSON syntax
        evaluator = JSONValidator()
        result = evaluator.evaluate(context)
        # Returns: True if valid JSON, False otherwise

        # Validate required keys
        evaluator = JSONValidator(required_keys=["name", "age"])
        result = evaluator.evaluate(context)
        # Returns: True if valid JSON with all required keys, False otherwise

        # Validate JSON in extracted field
        evaluator = JSONValidator(
            required_keys=["status"],
            output_extractor=lambda x: x.get("response", "") if isinstance(x, dict) else str(x)
        )
        # Validates the "response" field as JSON

    :param required_keys: Optional list of keys that must be present in the JSON object
    :param output_extractor: Optional function to extract/transform output_data before validation.
                             Must return a str, dict, list, or None. (default: None)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        required_keys: Optional[list] = None,
        output_extractor: Optional[Callable[[Any], Union[str, Dict, List, None]]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the JSONValidator evaluator.

        :param required_keys: List of keys that must be present in the parsed JSON
        :param output_extractor: Optional function to extract/transform output_data before validation.
                                 Must return a str, dict, list, or None.
        :param name: Optional custom name for the evaluator
        """
        super().__init__(name=name)

        if output_extractor is not None and not callable(output_extractor):
            raise TypeError("output_extractor must be a callable function")

        self.required_keys = required_keys or []
        self.output_extractor = output_extractor

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        """Perform JSON validation.

        :param context: The evaluation context
        :return: EvaluatorResult with boolean value and pass/fail assessment
        """
        output = context.output_data

        if self.output_extractor is not None:
            output = self.output_extractor(output)

        if output is None:
            return EvaluatorResult(value=False, assessment="fail")

        if isinstance(output, (dict, list)):
            parsed_data = output
        else:
            output_str = str(output)
            try:
                parsed_data = json.loads(output_str)
            except (json.JSONDecodeError, ValueError, TypeError):
                return EvaluatorResult(value=False, assessment="fail")

        if self.required_keys and isinstance(parsed_data, dict):
            for key in self.required_keys:
                if key not in parsed_data:
                    return EvaluatorResult(value=False, assessment="fail")

        return EvaluatorResult(value=True, assessment="pass")
