"""String matching evaluators for LLMObs."""

import re
from typing import Any
from typing import Dict
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
        # Returns: {"score": 1.0, "passed": True} if exact match

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

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform exact match evaluation.

        :param context: The evaluation context
        :return: Dictionary with 'score' (1.0 or 0.0), 'passed' (bool), and 'details'
        """
        output = context.output_data
        expected = context.expected_output

        # Handle None values
        if output is None and expected is None:
            return {"score": 1.0, "passed": True, "details": "Both values are None"}
        if output is None or expected is None:
            return {
                "score": 0.0,
                "passed": False,
                "details": f"One value is None: output={output}, expected={expected}",
            }

        # Convert to strings for comparison
        output_str = str(output)
        expected_str = str(expected)

        # Apply transformations
        if self.strip_whitespace:
            output_str = output_str.strip()
            expected_str = expected_str.strip()

        if not self.case_sensitive:
            output_str = output_str.lower()
            expected_str = expected_str.lower()

        # Compare
        passed = output_str == expected_str
        score = 1.0 if passed else 0.0

        return {
            "score": score,
            "passed": passed,
            "details": {
                "output_length": len(output_str),
                "expected_length": len(expected_str),
                "case_sensitive": self.case_sensitive,
                "strip_whitespace": self.strip_whitespace,
            },
        }


class RegexMatch(BaseEvaluator):
    r"""Evaluator that performs regex pattern matching.

    Checks if the output_data matches a given regex pattern.
    Useful for validating structured outputs like emails, phone numbers,
    URLs, or any custom format requirements.

    Example::

        # Validate email format
        evaluator = RegexMatch(pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        result = evaluator.evaluate(context)
        # Returns: {"score": 1.0, "passed": True, "matched_groups": [...]}

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

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform regex match evaluation.

        :param context: The evaluation context
        :return: Dictionary with 'score', 'passed', 'matched_groups', and 'details'
        """
        output = context.output_data

        # Handle None
        if output is None:
            return {
                "score": 0.0,
                "passed": False,
                "matched_groups": [],
                "details": "Output is None",
            }

        # Convert to string
        output_str = str(output)

        # Perform regex matching based on mode
        if self.match_mode == "search":
            match = self.pattern.search(output_str)
        elif self.match_mode == "match":
            match = self.pattern.match(output_str)
        else:  # fullmatch
            match = self.pattern.fullmatch(output_str)

        passed = match is not None
        score = 1.0 if passed else 0.0

        # Extract matched groups if any
        matched_groups = []
        if match:
            matched_groups = list(match.groups())

        return {
            "score": score,
            "passed": passed,
            "matched_groups": matched_groups,
            "details": {
                "pattern": self.pattern_str,
                "match_mode": self.match_mode,
                "output_length": len(output_str),
            },
        }
