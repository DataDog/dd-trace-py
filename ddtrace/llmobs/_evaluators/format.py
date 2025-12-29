"""Format validation evaluators for LLMObs."""

import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext


class LengthValidator(BaseEvaluator):
    """Evaluator that validates output length constraints.

    Checks if the output_data length falls within specified min/max bounds.
    Useful for ensuring responses meet length requirements, preventing
    overly verbose or too brief outputs.

    Example::

        # Ensure response is between 50-200 characters
        evaluator = LengthValidator(min_length=50, max_length=200, count_type="characters")
        result = evaluator.evaluate(context)
        # Returns: {"score": 1.0, "passed": True, "length": 150}

    :param min_length: Minimum allowed length (inclusive), None for no minimum
    :param max_length: Maximum allowed length (inclusive), None for no maximum
    :param count_type: What to count - 'characters', 'words', or 'lines'
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        count_type: str = "characters",
        name: Optional[str] = None,
    ):
        """Initialize the LengthValidator evaluator.

        :param min_length: Minimum allowed length (None for no minimum)
        :param max_length: Maximum allowed length (None for no maximum)
        :param count_type: One of 'characters', 'words', or 'lines'
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

        self.min_length = min_length
        self.max_length = max_length
        self.count_type = count_type

    def _calculate_length(self, text: str) -> int:
        """Calculate length based on count_type.

        :param text: The text to measure
        :return: The length value
        """
        if self.count_type == "characters":
            return len(text)
        elif self.count_type == "words":
            # Split on whitespace and filter out empty strings
            return len([word for word in text.split() if word])
        else:  # lines
            return len(text.splitlines())

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform length validation.

        :param context: The evaluation context
        :return: Dictionary with 'score', 'passed', 'length', and 'details'
        """
        output = context.output_data

        # Handle None
        if output is None:
            return {
                "score": 0.0,
                "passed": False,
                "length": 0,
                "details": "Output is None",
            }

        # Convert to string
        output_str = str(output)
        length = self._calculate_length(output_str)

        # Check constraints
        passed = True
        violations = []

        if self.min_length is not None and length < self.min_length:
            passed = False
            violations.append(f"length {length} < min {self.min_length}")

        if self.max_length is not None and length > self.max_length:
            passed = False
            violations.append(f"length {length} > max {self.max_length}")

        score = 1.0 if passed else 0.0

        return {
            "score": score,
            "passed": passed,
            "length": length,
            "details": {
                "count_type": self.count_type,
                "min_length": self.min_length,
                "max_length": self.max_length,
                "violations": violations,
            },
        }


class JSONValidator(BaseEvaluator):
    """Evaluator that validates if output is valid JSON.

    Checks if the output_data can be parsed as valid JSON.
    Optionally validates against a specific schema structure.

    Example::

        # Just validate JSON syntax
        evaluator = JSONValidator()
        result = evaluator.evaluate(context)
        # Returns: {"score": 1.0, "passed": True, "parsed_data": {...}}

        # Validate required keys
        evaluator = JSONValidator(required_keys=["name", "age"])
        result = evaluator.evaluate(context)

    :param required_keys: Optional list of keys that must be present in the JSON object
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        required_keys: Optional[list] = None,
        name: Optional[str] = None,
    ):
        """Initialize the JSONValidator evaluator.

        :param required_keys: List of keys that must be present in the parsed JSON
        :param name: Optional custom name for the evaluator
        """
        super().__init__(name=name)
        self.required_keys = required_keys or []

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform JSON validation.

        :param context: The evaluation context
        :return: Dictionary with 'score', 'passed', 'parsed_data', and 'details'
        """
        output = context.output_data

        # Handle None
        if output is None:
            return {
                "score": 0.0,
                "passed": False,
                "parsed_data": None,
                "details": "Output is None",
            }

        # Convert to string if needed
        if isinstance(output, (dict, list)):
            # Already a JSON-compatible structure
            parsed_data = output
        else:
            output_str = str(output)
            try:
                parsed_data = json.loads(output_str)
            except (json.JSONDecodeError, ValueError) as e:
                return {
                    "score": 0.0,
                    "passed": False,
                    "parsed_data": None,
                    "details": f"Invalid JSON: {str(e)}",
                }

        # Check required keys if specified
        missing_keys = []
        if self.required_keys and isinstance(parsed_data, dict):
            for key in self.required_keys:
                if key not in parsed_data:
                    missing_keys.append(key)

        passed = len(missing_keys) == 0
        score = 1.0 if passed else 0.0

        details = {"is_valid_json": True}
        if self.required_keys:
            details["required_keys"] = self.required_keys
            if missing_keys:
                details["missing_keys"] = missing_keys

        return {
            "score": score,
            "passed": passed,
            "parsed_data": parsed_data,
            "details": details,
        }
