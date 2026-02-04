"""Tests for built-in LLMObs evaluators."""

import pytest

from ddtrace.llmobs._evaluators.format import JSONValidator
from ddtrace.llmobs._evaluators.format import LengthValidator
from ddtrace.llmobs._evaluators.semantic import SemanticSimilarity
from ddtrace.llmobs._evaluators.string_matching import RegexMatch
from ddtrace.llmobs._evaluators.string_matching import StringCheck
from ddtrace.llmobs._experiment import EvaluatorContext


class TestStringCheck:
    def test_exact_match(self):
        evaluator = StringCheck(operation="eq")
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_case_insensitive(self):
        evaluator = StringCheck(operation="eq", case_sensitive=False)
        ctx = EvaluatorContext(input_data={}, output_data="Hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_contains(self):
        evaluator = StringCheck(operation="contains")
        ctx = EvaluatorContext(input_data={}, output_data="hello world", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"


class TestRegexMatch:
    def test_match(self):
        evaluator = RegexMatch(pattern=r"\d{3}-\d{4}")
        ctx = EvaluatorContext(input_data={}, output_data="Call 555-1234")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data="No number")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_fullmatch_mode(self):
        evaluator = RegexMatch(pattern=r"\d{3}-\d{4}", match_mode="fullmatch")
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
            RegexMatch(pattern=r"[invalid(")


class TestLengthValidator:
    def test_within_range(self):
        evaluator = LengthValidator(min_length=5, max_length=20)
        ctx = EvaluatorContext(input_data={}, output_data="hello world")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_out_of_range(self):
        evaluator = LengthValidator(min_length=10, max_length=20)
        ctx = EvaluatorContext(input_data={}, output_data="short")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_word_count(self):
        evaluator = LengthValidator(min_length=2, max_length=5, count_type="words")
        ctx = EvaluatorContext(input_data={}, output_data="one two three")
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_invalid_range(self):
        with pytest.raises(ValueError, match="min_length .* cannot be greater than max_length"):
            LengthValidator(min_length=10, max_length=5)


class TestJSONValidator:
    def test_valid_json(self):
        evaluator = JSONValidator()
        ctx = EvaluatorContext(input_data={}, output_data='{"name": "John"}')
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data={"name": "John"})
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

    def test_invalid_json(self):
        evaluator = JSONValidator()
        ctx = EvaluatorContext(input_data={}, output_data="not json {")
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"

    def test_required_keys(self):
        evaluator = JSONValidator(required_keys=["name", "age"])
        ctx = EvaluatorContext(input_data={}, output_data='{"name": "John", "age": 30}')
        result = evaluator.evaluate(ctx)
        assert result.value is True
        assert result.assessment == "pass"

        ctx = EvaluatorContext(input_data={}, output_data='{"name": "John"}')
        result = evaluator.evaluate(ctx)
        assert result.value is False
        assert result.assessment == "fail"


class TestSemanticSimilarity:
    def test_identical_embeddings(self):
        evaluator = SemanticSimilarity(embedding_fn=lambda x: [1.0, 0.0])
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value == 1.0
        assert result.assessment == "pass"

    def test_orthogonal_embeddings(self):
        def embedding_fn(text):
            return [1.0, 0.0] if "hello" in text else [0.0, 1.0]

        evaluator = SemanticSimilarity(embedding_fn=embedding_fn)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value == 0.5
        assert result.assessment == "fail"

    def test_embedding_error(self):
        def failing_fn(text):
            raise RuntimeError("API error")

        evaluator = SemanticSimilarity(embedding_fn=failing_fn)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        with pytest.raises(RuntimeError, match="API error"):
            evaluator.evaluate(ctx)

    def test_threshold_pass(self):
        evaluator = SemanticSimilarity(embedding_fn=lambda x: [1.0, 0.0], threshold=0.9)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="hello")
        result = evaluator.evaluate(ctx)
        assert result.value == 1.0
        assert result.assessment == "pass"

    def test_threshold_fail(self):
        def embedding_fn(text):
            return [1.0, 0.0] if "hello" in text else [0.0, 1.0]

        evaluator = SemanticSimilarity(embedding_fn=embedding_fn, threshold=0.6)
        ctx = EvaluatorContext(input_data={}, output_data="hello", expected_output="world")
        result = evaluator.evaluate(ctx)
        assert result.value == 0.5
        assert result.assessment == "fail"
