"""String matching evaluators for LLMObs."""

import re
from typing import Optional
from typing import Pattern

from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext


class ExactMatch(BaseEvaluator):
    """Evaluator that performs exact string matching.

    Compares the output_data with expected_output for exact equality.
    Useful for classification tasks, structured outputs, or any scenario
    where the output must match exactly.

    Example::

        evaluator = ExactMatch(case_sensitive=True)
        result = evaluator.evaluate(context)
        # Returns: 1.0 if exact match, 0.0 otherwise

    :param case_sensitive: Whether to perform case-sensitive comparison (default: True)
    :param strip_whitespace: Whether to strip leading/trailing whitespace before comparison (default: False)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        case_sensitive: bool = True,
        strip_whitespace: bool = False,
        name: Optional[str] = None,
    ):
        """Initialize the ExactMatch evaluator.

        :param case_sensitive: Whether to perform case-sensitive comparison
        :param strip_whitespace: Whether to strip whitespace before comparison
        :param name: Optional custom name for the evaluator
        """
        super().__init__(name=name)
        self.case_sensitive = case_sensitive
        self.strip_whitespace = strip_whitespace

    def evaluate(self, context: EvaluatorContext) -> float:
        """Perform exact match evaluation.

        :param context: The evaluation context
        :return: 1.0 if exact match, 0.0 otherwise
        """
        output = context.output_data
        expected = context.expected_output

        if output is None and expected is None:
            return 1.0
        if output is None or expected is None:
            return 0.0

        output_str = str(output)
        expected_str = str(expected)

        if self.strip_whitespace:
            output_str = output_str.strip()
            expected_str = expected_str.strip()

        if not self.case_sensitive:
            output_str = output_str.lower()
            expected_str = expected_str.lower()

        return 1.0 if output_str == expected_str else 0.0


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

    :param pattern: The regex pattern to match against
    :param match_mode: How to match - 'search' (partial), 'match' (from start), or 'fullmatch' (entire string)
    :param flags: Optional regex flags (e.g., re.IGNORECASE)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        pattern: str,
        match_mode: str = "search",
        flags: int = 0,
        name: Optional[str] = None,
    ):
        """Initialize the RegexMatch evaluator.

        :param pattern: The regex pattern string
        :param match_mode: One of 'search', 'match', or 'fullmatch'
        :param flags: Regex flags (e.g., re.IGNORECASE, re.MULTILINE)
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

    def evaluate(self, context: EvaluatorContext) -> float:
        """Perform regex match evaluation.

        :param context: The evaluation context
        :return: 1.0 if pattern matches, 0.0 otherwise
        """
        output = context.output_data

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
