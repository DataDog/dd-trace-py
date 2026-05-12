"""Tests for built-in LLMObs evaluators."""

import pytest

from ddtrace.llmobs._evaluators.format import JSONEvaluator
from ddtrace.llmobs._evaluators.format import LengthEvaluator
from ddtrace.llmobs._evaluators.semantic import SemanticSimilarityEvaluator
from ddtrace.llmobs._evaluators.string_matching import RegexMatchEvaluator
from ddtrace.llmobs._evaluators.string_matching import StringCheckEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext


class TestStringCheckEvaluator:
    def test_exact_match(self):
        evaluator = StringCheckEvaluator(operation="eq")
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_case_insensitive(self):
        evaluator = StringCheckEvaluator(operation="eq", case_sensitive=False)
        ctx = EvaluatorContext(input_data={}, output_data="Hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_contains(self):
        evaluator = StringCheckEvaluator(operation="contains")
        ctx = EvaluatorContext(input_data={}, output_data="hello world", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_not_equals(self):
        evaluator = StringCheckEvaluator(operation="ne")
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_icontains(self):
        evaluator = StringCheckEvaluator(operation="icontains")
        ctx = EvaluatorContext(input_data={}, output_data="Hello World", expected_output="WORLD")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="Hello World", expected_output="foo")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_strip_whitespace(self):
        evaluator = StringCheckEvaluator(operation="eq", strip_whitespace=True)
        ctx = EvaluatorContext(input_data={}, output_data="  hello  ", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_none_handling(self):
        evaluator = StringCheckEvaluator(operation="eq")

        ctx = EvaluatorContext(input_data={}, output_data=None, expected_output=None)
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output=None)
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

        ctx = EvaluatorContext(input_data={}, output_data=None, expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

        evaluator_ne = StringCheckEvaluator(operation="ne")
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output=None)
        result = evaluator_ne.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"


class TestRegexMatchEvaluator:
    def test_match(self):
        evaluator = RegexMatchEvaluator(pattern=r"\d{3}-\d{4}")
        ctx = EvaluatorContext(input_data={}, output_data="Call 555-1234")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="No number")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_search_mode(self):
        evaluator = RegexMatchEvaluator(pattern=r"\d{3}-\d{4}", match_mode="search")
        ctx = EvaluatorContext(input_data={}, output_data="Call 555-1234 now")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_fullmatch_mode(self):
        evaluator = RegexMatchEvaluator(pattern=r"\d{3}-\d{4}", match_mode="fullmatch")
        ctx = EvaluatorContext(input_data={}, output_data="555-1234")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="Call 555-1234")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_invalid_pattern(self):
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            RegexMatchEvaluator(pattern=r"[invalid(")

    def test_none_handling(self):
        evaluator = RegexMatchEvaluator(pattern=r"\d+")
        ctx = EvaluatorContext(input_data={}, output_data=None)
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"


class TestLengthEvaluator:
    def test_within_range(self):
        evaluator = LengthEvaluator(min_length=5, max_length=20)
        ctx = EvaluatorContext(input_data={}, output_data="hello world")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_out_of_range(self):
        evaluator = LengthEvaluator(min_length=10, max_length=20)
        ctx = EvaluatorContext(input_data={}, output_data="short")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_word_count(self):
        evaluator = LengthEvaluator(min_length=2, max_length=5, count_type="words")
        ctx = EvaluatorContext(input_data={}, output_data="one two three")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_invalid_range(self):
        with pytest.raises(ValueError, match="min_length .* cannot be greater than max_length"):
            LengthEvaluator(min_length=10, max_length=5)

    def test_none_handling(self):
        evaluator = LengthEvaluator(min_length=1)
        ctx = EvaluatorContext(input_data={}, output_data=None)
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"


class TestJSONEvaluator:
    def test_valid_json(self):
        evaluator = JSONEvaluator()
        ctx = EvaluatorContext(input_data={}, output_data='{"name": "John"}')
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data={"name": "John"})
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_invalid_json(self):
        evaluator = JSONEvaluator()
        ctx = EvaluatorContext(input_data={}, output_data="not json {")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_required_keys(self):
        evaluator = JSONEvaluator(required_keys=["name", "age"])
        ctx = EvaluatorContext(input_data={}, output_data='{"name": "John", "age": 30}')
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data='{"name": "John"}')
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_none_handling(self):
        evaluator = JSONEvaluator()
        ctx = EvaluatorContext(input_data={}, output_data=None)
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"


class TestSemanticSimilarityEvaluator:
    def test_identical_embeddings(self):
        evaluator = SemanticSimilarityEvaluator(embedding_fn=lambda x: [1.0, 0.0])
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value == 1.0
        assert result.assessment == "pass"

    def test_orthogonal_embeddings(self):
        def embedding_fn(text):
            return [1.0, 0.0] if "hello" in text else [0.0, 1.0]

        evaluator = SemanticSimilarityEvaluator(embedding_fn=embedding_fn)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value == 0.5
        assert result.assessment == "fail"

    def test_embedding_error(self):
        def failing_fn(text):
            raise RuntimeError("API error")

        evaluator = SemanticSimilarityEvaluator(embedding_fn=failing_fn)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        with pytest.raises(RuntimeError, match="API error"):
            evaluator.evaluate(ctx)

    def test_threshold_pass(self):
        evaluator = SemanticSimilarityEvaluator(embedding_fn=lambda x: [1.0, 0.0], threshold=0.9)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value == 1.0
        assert result.assessment == "pass"

    def test_threshold_fail(self):
        def embedding_fn(text):
            return [1.0, 0.0] if "hello" in text else [0.0, 1.0]

        evaluator = SemanticSimilarityEvaluator(embedding_fn=embedding_fn, threshold=0.6)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value == 0.5
        assert result.assessment == "fail"

    def test_none_handling(self):
        evaluator = SemanticSimilarityEvaluator(embedding_fn=lambda x: [1.0, 0.0])

        ctx = EvaluatorContext(input_data={}, output_data=None, expected_output=None)
        result = evaluator.evaluate(ctx)
        assert result.value == 1.0
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output=None)
        result = evaluator.evaluate(ctx)
        assert result.value == 0.0
        assert result.assessment == "fail"

        ctx = EvaluatorContext(input_data={}, output_data=None, expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value == 0.0
        assert result.assessment == "fail"
