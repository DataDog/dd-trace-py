"""String matching evaluators for LLMObs."""

import re
from typing import Any
from typing import Callable
from typing import Optional
from typing import Pattern

from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext


class StringCheck(BaseEvaluator):
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
        evaluator = StringCheck(operation="eq", case_sensitive=True)
        result = evaluator.evaluate(context)
        # Returns: 1.0 if output == expected_output

        # Not equals
        evaluator = StringCheck(operation="ne")
        # Returns: 1.0 if output != expected_output

        # Contains (case-sensitive)
        evaluator = StringCheck(operation="contains")
        # Returns: 1.0 if expected_output is in output

        # Contains (case-insensitive)
        evaluator = StringCheck(operation="icontains")
        # Returns: 1.0 if expected_output is in output (ignoring case)

        # Extract field from dict output
        evaluator = StringCheck(
            operation="eq",
            output_extractor=lambda x: x.get("message", "") if isinstance(x, dict) else str(x),
        )
        # Extracts "message" field from dict before comparison

    :param operation: String comparison operation: 'eq', 'ne', 'contains', 'icontains' (default: 'eq')
    :param case_sensitive: Whether to perform case-sensitive comparison (default: True, ignored for 'icontains')
    :param strip_whitespace: Whether to strip leading/trailing whitespace before comparison (default: False)
    :param output_extractor: Optional function to extract/transform output_data before comparison
                             (default: None)
    :param expected_output_extractor: Optional function to extract/transform expected_output before
                                      comparison (default: None)
    :param name: Optional custom name for the evaluator
    """

    VALID_OPERATIONS = ("eq", "ne", "contains", "icontains")

    def __init__(
        self,
        operation: str = "eq",
        case_sensitive: bool = True,
        strip_whitespace: bool = False,
        output_extractor: Optional[Callable[[Any], Any]] = None,
        expected_output_extractor: Optional[Callable[[Any], Any]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the StringCheck evaluator.

        :param operation: String comparison operation: 'eq', 'ne', 'contains', 'icontains'
        :param case_sensitive: Whether to perform case-sensitive comparison
        :param strip_whitespace: Whether to strip whitespace before comparison
        :param output_extractor: Optional function to extract/transform output_data before comparison
        :param expected_output_extractor: Optional function to extract/transform expected_output before comparison
        :param name: Optional custom name for the evaluator
        :raises ValueError: If operation is invalid
        """
        super().__init__(name=name)

        if operation not in self.VALID_OPERATIONS:
            raise ValueError(f"operation must be one of {self.VALID_OPERATIONS}, got: {operation}")

        self.operation = operation
        self.case_sensitive = case_sensitive
        self.strip_whitespace = strip_whitespace
        self.output_extractor = output_extractor
        self.expected_output_extractor = expected_output_extractor

    def evaluate(self, context: EvaluatorContext) -> float:
        """Perform string comparison evaluation.

        :param context: The evaluation context
        :return: 1.0 if condition passes, 0.0 otherwise
        """
        output = context.output_data
        expected = context.expected_output

        # Apply extraction functions if provided
        if self.output_extractor is not None:
            output = self.output_extractor(output)
        if self.expected_output_extractor is not None:
            expected = self.expected_output_extractor(expected)

        # Handle None cases
        if output is None and expected is None:
            # Both None
            if self.operation == "eq":
                return 1.0  # None == None
            elif self.operation == "ne":
                return 0.0  # Not (None != None)
            else:  # contains, icontains
                return 0.0  # Can't check containment with None

        if output is None or expected is None:
            # One is None
            if self.operation == "eq":
                return 0.0  # None != value
            elif self.operation == "ne":
                return 1.0  # None != value
            else:  # contains, icontains
                return 0.0  # Can't check containment with None

        output_str = str(output)
        expected_str = str(expected)

        if self.strip_whitespace:
            output_str = output_str.strip()
            expected_str = expected_str.strip()

        # Apply case sensitivity
        if self.operation == "icontains":
            # icontains is always case-insensitive
            output_str = output_str.lower()
            expected_str = expected_str.lower()
        elif not self.case_sensitive:
            output_str = output_str.lower()
            expected_str = expected_str.lower()

        # Perform operation
        if self.operation == "eq":
            return 1.0 if output_str == expected_str else 0.0
        elif self.operation == "ne":
            return 1.0 if output_str != expected_str else 0.0
        elif self.operation in ("contains", "icontains"):
            return 1.0 if expected_str in output_str else 0.0

        return 0.0  # Should never reach here


class RegexMatch(BaseEvaluator):
    r"""Evaluator that performs regex pattern matching.

    Checks if the output_data matches a given regex pattern.
    Useful for validating structured outputs like emails, phone numbers,
    URLs, or any custom format requirements.

    Example::

        # Validate email format
        evaluator = RegexMatch(pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        result = evaluator.evaluate(context)
        # Returns: 1.0 if pattern matches, 0.0 otherwise

        # Extract field from dict output before regex matching
        evaluator = RegexMatch(
            pattern=r"\d{3}-\d{4}",
            output_extractor=lambda x: x.get("phone", "") if isinstance(x, dict) else str(x)
        )
        # Extracts "phone" field from dict before pattern matching

    :param pattern: The regex pattern to match against
    :param match_mode: How to match - 'search' (partial), 'match' (from start), or 'fullmatch' (entire string)
    :param flags: Optional regex flags (e.g., re.IGNORECASE)
    :param output_extractor: Optional function to extract/transform output_data before matching (default: None)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        pattern: str,
        match_mode: str = "search",
        flags: int = 0,
        output_extractor: Optional[Callable[[Any], Any]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the RegexMatch evaluator.

        :param pattern: The regex pattern string
        :param match_mode: One of 'search', 'match', or 'fullmatch'
        :param flags: Regex flags (e.g., re.IGNORECASE, re.MULTILINE)
        :param output_extractor: Optional function to extract/transform output_data before matching
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

        self.pattern_str = pattern
        self.match_mode = match_mode
        self.flags = flags
        self.output_extractor = output_extractor

    def evaluate(self, context: EvaluatorContext) -> float:
        """Perform regex match evaluation.

        :param context: The evaluation context
        :return: 1.0 if pattern matches, 0.0 otherwise
        """
        output = context.output_data

        # Apply extraction function if provided
        if self.output_extractor is not None:
            output = self.output_extractor(output)

        if output is None:
            return 0.0

        output_str = str(output)

        if self.match_mode == "search":
            match = self.pattern.search(output_str)
        elif self.match_mode == "match":
            match = self.pattern.match(output_str)
        else:
            match = self.pattern.fullmatch(output_str)

        return 1.0 if match is not None else 0.0
