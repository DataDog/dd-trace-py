"""String matching evaluators for LLMObs."""

import re
from typing import Any
from typing import Callable
from typing import Optional
from typing import Pattern

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult


class StringCheckEvaluator(BaseEvaluator):
    """Evaluator that performs string comparison operations.

    Compares the output_data with expected_output using various string operations.
    Supports operations similar to OpenAI's String Check Grader.

    Operations:
    - 'eq': Equals (exact match) - default
    - 'ne': Not equals (inequality)
    - 'contains': Contains substring (case-sensitive)
    - 'icontains': Contains substring (case-insensitive)

    Example::

        # Exact match (default)
        evaluator = StringCheckEvaluator(operation="eq", case_sensitive=True)
        result = evaluator.evaluate(context)
        # Returns: True if output == expected_output

        # Not equals
        evaluator = StringCheckEvaluator(operation="ne")
        # Returns: True if output != expected_output

        # Contains (case-sensitive)
        evaluator = StringCheckEvaluator(operation="contains")
        # Returns: True if expected_output is in output

        # Contains (case-insensitive)
        evaluator = StringCheckEvaluator(operation="icontains")
        # Returns: True if expected_output is in output (ignoring case)

        # Extract field from dict output
        evaluator = StringCheckEvaluator(
            operation="eq",
            output_extractor=lambda x: x.get("message", "") if isinstance(x, dict) else str(x),
        )
        # Extracts "message" field from dict before comparison

    :param operation: String comparison operation: 'eq', 'ne', 'contains', 'icontains' (default: 'eq')
    :param case_sensitive: Whether to perform case-sensitive comparison (default: True, ignored for 'icontains')
    :param strip_whitespace: Whether to strip leading/trailing whitespace before comparison (default: False)
    :param output_extractor: Optional function to extract/transform output_data before comparison.
                             Must return a str or None. (default: None)
    :param expected_output_extractor: Optional function to extract/transform expected_output before
                                      comparison. Must return a str or None. (default: None)
    :param name: Optional custom name for the evaluator
    """

    VALID_OPERATIONS = ("eq", "ne", "contains", "icontains")

    def __init__(
        self,
        operation: str = "eq",
        case_sensitive: bool = True,
        strip_whitespace: bool = False,
        output_extractor: Optional[Callable[[Any], Optional[str]]] = None,
        expected_output_extractor: Optional[Callable[[Any], Optional[str]]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the StringCheckEvaluator evaluator.

        :param operation: String comparison operation: 'eq', 'ne', 'contains', 'icontains'
        :param case_sensitive: Whether to perform case-sensitive comparison
        :param strip_whitespace: Whether to strip whitespace before comparison
        :param output_extractor: Optional function to extract/transform output_data before comparison.
                                 Must return a str or None.
        :param expected_output_extractor: Optional function to extract/transform expected_output before
                                          comparison. Must return a str or None.
        :param name: Optional custom name for the evaluator
        :raises ValueError: If operation is invalid
        """
        super().__init__(name=name)

        if operation not in self.VALID_OPERATIONS:
            raise ValueError(f"operation must be one of {self.VALID_OPERATIONS}, got: {operation}")

        if output_extractor is not None and not callable(output_extractor):
            raise TypeError("output_extractor must be a callable function")

        if expected_output_extractor is not None and not callable(expected_output_extractor):
            raise TypeError("expected_output_extractor must be a callable function")

        self.operation = operation
        self.case_sensitive = case_sensitive
        self.strip_whitespace = strip_whitespace
        self.output_extractor = output_extractor
        self.expected_output_extractor = expected_output_extractor

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        """Perform string comparison evaluation.

        :param context: The evaluation context
        :return: EvaluatorResult with boolean value and pass/fail assessment
        """
        output = context.output_data
        expected = context.expected_output

        if self.output_extractor is not None:
            output = self.output_extractor(output)
        if self.expected_output_extractor is not None:
            expected = self.expected_output_extractor(expected)

        if output is None and expected is None:
            result = self.operation == "eq"
            return EvaluatorResult(value=result, assessment="pass" if result else "fail")

        if output is None or expected is None:
            if self.operation == "eq":
                return EvaluatorResult(value=False, assessment="fail")
            elif self.operation == "ne":
                return EvaluatorResult(value=True, assessment="pass")
            else:
                return EvaluatorResult(value=False, assessment="fail")

        output_str = str(output)
        expected_str = str(expected)

        if self.strip_whitespace:
            output_str = output_str.strip()
            expected_str = expected_str.strip()

        if self.operation == "icontains" or not self.case_sensitive:
            output_str = output_str.lower()
            expected_str = expected_str.lower()

        if self.operation == "eq":
            result = output_str == expected_str
        elif self.operation == "ne":
            result = output_str != expected_str
        else:
            result = expected_str in output_str

        return EvaluatorResult(value=result, assessment="pass" if result else "fail")


class RegexMatchEvaluator(BaseEvaluator):
    r"""Evaluator that performs regex pattern matching.

    Checks if the output_data matches a given regex pattern.
    Useful for validating structured outputs like emails, phone numbers,
    URLs, or any custom format requirements.

    Example::

        # Validate email format
        evaluator = RegexMatchEvaluator(pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        result = evaluator.evaluate(context)
        # Returns: True if pattern matches, False otherwise

        # Extract field from dict output before regex matching
        evaluator = RegexMatchEvaluator(
            pattern=r"\d{3}-\d{4}",
            output_extractor=lambda x: x.get("phone", "") if isinstance(x, dict) else str(x)
        )
        # Extracts "phone" field from dict before pattern matching

    :param pattern: The regex pattern to match against
    :param match_mode: How to match - 'search' (partial), 'match' (from start), or 'fullmatch' (entire string)
    :param flags: Optional regex flags (e.g., re.IGNORECASE)
    :param output_extractor: Optional function to extract/transform output_data before matching.
                             Must return a str or None. (default: None)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        pattern: str,
        match_mode: str = "search",
        flags: int = 0,
        output_extractor: Optional[Callable[[Any], Optional[str]]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the RegexMatchEvaluator evaluator.

        :param pattern: The regex pattern string
        :param match_mode: One of 'search', 'match', or 'fullmatch'
        :param flags: Regex flags (e.g., re.IGNORECASE, re.MULTILINE)
        :param output_extractor: Optional function to extract/transform output_data before matching.
                                 Must return a str or None.
        :param name: Optional custom name for the evaluator
        :raises ValueError: If match_mode is invalid or pattern is invalid
        """
        super().__init__(name=name)

        if match_mode not in ("search", "match", "fullmatch"):
            raise ValueError(f"match_mode must be 'search', 'match', or 'fullmatch', got: {match_mode}")

        try:
            self.pattern: Pattern = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")

        if output_extractor is not None and not callable(output_extractor):
            raise TypeError("output_extractor must be a callable function")

        self.pattern_str = pattern
        self.match_mode = match_mode
        self.flags = flags
        self.output_extractor = output_extractor

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        """Perform regex match evaluation.

        :param context: The evaluation context
        :return: EvaluatorResult with boolean value and pass/fail assessment
        """
        output = context.output_data

        if self.output_extractor is not None:
            output = self.output_extractor(output)

        if output is None:
            return EvaluatorResult(value=False, assessment="fail")

        output_str = str(output)

        if self.match_mode == "search":
            match = self.pattern.search(output_str)
        elif self.match_mode == "match":
            match = self.pattern.match(output_str)
        else:
            match = self.pattern.fullmatch(output_str)

        result = match is not None
        return EvaluatorResult(value=result, assessment="pass" if result else "fail")
