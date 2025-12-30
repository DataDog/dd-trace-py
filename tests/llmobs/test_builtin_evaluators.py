"""Tests for built-in LLMObs evaluators."""

import pytest

from ddtrace.llmobs._evaluators.base import EvaluatorContext
from ddtrace.llmobs._evaluators.format import JSONValidator
from ddtrace.llmobs._evaluators.format import LengthValidator
from ddtrace.llmobs._evaluators.semantic import AnswerRelevancy
from ddtrace.llmobs._evaluators.semantic import SemanticSimilarity
from ddtrace.llmobs._evaluators.string_matching import ExactMatch
from ddtrace.llmobs._evaluators.string_matching import RegexMatch


class TestExactMatch:
    def test_exact_match_success(self):
        evaluator = ExactMatch()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello world",
            expected_output="hello world",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_exact_match_failure(self):
        evaluator = ExactMatch()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello world",
            expected_output="goodbye world",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_exact_match_case_insensitive(self):
        evaluator = ExactMatch(case_sensitive=False)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="Hello World",
            expected_output="hello world",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_exact_match_strip_whitespace(self):
        evaluator = ExactMatch(strip_whitespace=True)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="  hello world  ",
            expected_output="hello world",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_exact_match_both_none(self):
        evaluator = ExactMatch()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data=None,
            expected_output=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_exact_match_one_none(self):
        evaluator = ExactMatch()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello",
            expected_output=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0


class TestRegexMatch:
    def test_regex_match_success(self):
        evaluator = RegexMatch(pattern=r"\d{3}-\d{4}")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="Call me at 555-1234",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_regex_match_failure(self):
        evaluator = RegexMatch(pattern=r"\d{3}-\d{4}")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="No phone number here",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_regex_match_with_groups(self):
        evaluator = RegexMatch(pattern=r"(\d{3})-(\d{4})")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="555-1234",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_regex_match_mode_fullmatch(self):
        evaluator = RegexMatch(pattern=r"\d{3}-\d{4}", match_mode="fullmatch")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="555-1234",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

        # Should fail with extra text
        ctx2 = EvaluatorContext(
            input_data={"query": "test"},
            output_data="Call 555-1234",
        )
        result2 = evaluator.evaluate(ctx2)
        assert result2 == 0.0

    def test_regex_match_case_insensitive(self):
        import re

        evaluator = RegexMatch(pattern=r"hello", flags=re.IGNORECASE)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="HELLO WORLD",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_regex_match_invalid_pattern(self):
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            RegexMatch(pattern=r"[invalid(")

    def test_regex_match_invalid_mode(self):
        with pytest.raises(ValueError, match="match_mode must be"):
            RegexMatch(pattern=r"test", match_mode="invalid")

    def test_regex_match_none_output(self):
        evaluator = RegexMatch(pattern=r"\d+")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0


class TestLengthValidator:
    def test_length_validator_within_range(self):
        evaluator = LengthValidator(min_length=5, max_length=20)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello world",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_length_validator_too_short(self):
        evaluator = LengthValidator(min_length=10, max_length=20)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="short",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_length_validator_too_long(self):
        evaluator = LengthValidator(min_length=5, max_length=10)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="this is a very long response",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_length_validator_only_min(self):
        evaluator = LengthValidator(min_length=5)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello world this is a long text",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_length_validator_only_max(self):
        evaluator = LengthValidator(max_length=10)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="short",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_length_validator_words(self):
        evaluator = LengthValidator(min_length=2, max_length=5, count_type="words")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello world test",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_length_validator_lines(self):
        evaluator = LengthValidator(min_length=2, max_length=5, count_type="lines")
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="line1\nline2\nline3",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_length_validator_invalid_count_type(self):
        with pytest.raises(ValueError, match="count_type must be"):
            LengthValidator(min_length=5, count_type="invalid")

    def test_length_validator_invalid_range(self):
        with pytest.raises(ValueError, match="min_length .* cannot be greater than max_length"):
            LengthValidator(min_length=10, max_length=5)

    def test_length_validator_no_bounds(self):
        with pytest.raises(ValueError, match="At least one of min_length or max_length"):
            LengthValidator()

    def test_length_validator_none_output(self):
        evaluator = LengthValidator(min_length=5)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0


class TestJSONValidator:
    def test_json_validator_valid_json_string(self):
        evaluator = JSONValidator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data='{"name": "John", "age": 30}',
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_json_validator_valid_json_dict(self):
        evaluator = JSONValidator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data={"name": "John", "age": 30},
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_json_validator_invalid_json(self):
        evaluator = JSONValidator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="not valid json {",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_json_validator_with_required_keys(self):
        evaluator = JSONValidator(required_keys=["name", "age"])
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data='{"name": "John", "age": 30, "city": "NYC"}',
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_json_validator_missing_required_keys(self):
        evaluator = JSONValidator(required_keys=["name", "age", "email"])
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data='{"name": "John", "age": 30}',
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_json_validator_none_output(self):
        evaluator = JSONValidator()
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0


class TestSemanticSimilarity:
    def test_semantic_similarity_identical(self):
        """Test with identical embeddings."""

        def dummy_embedding(text):
            # Return same embedding for any text
            return [1.0, 0.0, 0.0]

        evaluator = SemanticSimilarity(embedding_fn=dummy_embedding, threshold=0.9)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello",
            expected_output="hello",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_semantic_similarity_different(self):
        """Test with orthogonal embeddings."""

        def dummy_embedding(text):
            # Return different embeddings based on text
            if "hello" in text:
                return [1.0, 0.0]
            else:
                return [0.0, 1.0]

        evaluator = SemanticSimilarity(embedding_fn=dummy_embedding, threshold=0.9)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello",
            expected_output="goodbye",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0
        # Orthogonal vectors have cosine similarity of 0, normalized to 0.5
        assert result == 0.5

    def test_semantic_similarity_threshold(self):
        """Test threshold behavior."""

        def dummy_embedding(text):
            return [0.8, 0.6]

        evaluator_high = SemanticSimilarity(embedding_fn=dummy_embedding, threshold=0.95)
        evaluator_low = SemanticSimilarity(embedding_fn=dummy_embedding, threshold=0.5)

        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="text1",
            expected_output="text2",
        )

        result_high = evaluator_high.evaluate(ctx)
        result_low = evaluator_low.evaluate(ctx)

        # Same similarity score
        assert result_high["similarity"] == result_low["similarity"]
        # But different pass/fail based on threshold
        assert result_high["passed"] is False
        assert result_low["passed"] is True

    def test_semantic_similarity_both_none(self):
        def dummy_embedding(text):
            return [1.0, 0.0]

        evaluator = SemanticSimilarity(embedding_fn=dummy_embedding)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data=None,
            expected_output=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_semantic_similarity_one_none(self):
        def dummy_embedding(text):
            return [1.0, 0.0]

        evaluator = SemanticSimilarity(embedding_fn=dummy_embedding)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello",
            expected_output=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_semantic_similarity_invalid_threshold(self):
        def dummy_embedding(text):
            return [1.0, 0.0]

        with pytest.raises(ValueError, match="threshold must be between 0 and 1"):
            SemanticSimilarity(embedding_fn=dummy_embedding, threshold=1.5)

    def test_semantic_similarity_embedding_error(self):
        def failing_embedding(text):
            raise RuntimeError("API error")

        evaluator = SemanticSimilarity(embedding_fn=failing_embedding)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello",
            expected_output="world",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_semantic_similarity_async(self):
        """Test async evaluation."""

        async def async_embedding(text):
            return [1.0, 0.0, 0.0]

        evaluator = SemanticSimilarity(embedding_fn=async_embedding)
        ctx = EvaluatorContext(
            input_data={"query": "test"},
            output_data="hello",
            expected_output="hello",
        )
        result = await evaluator.evaluate_async(ctx)
        assert result == 1.0


class TestAnswerRelevancy:
    def test_answer_relevancy_relevant(self):
        """Test with relevant answer."""

        def dummy_embedding(text):
            # Return same embedding for both
            return [1.0, 0.0, 0.0]

        evaluator = AnswerRelevancy(embedding_fn=dummy_embedding, threshold=0.9)
        ctx = EvaluatorContext(
            input_data={"question": "What is Python?"},
            output_data="Python is a programming language",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_answer_relevancy_irrelevant(self):
        """Test with irrelevant answer."""

        def dummy_embedding(text):
            if "Python" in text:
                return [1.0, 0.0]
            else:
                return [0.0, 1.0]

        evaluator = AnswerRelevancy(embedding_fn=dummy_embedding, threshold=0.9)
        ctx = EvaluatorContext(
            input_data={"question": "What is Python?"},
            output_data="I like pizza",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_answer_relevancy_with_input_key(self):
        """Test extracting specific key from input_data."""

        def dummy_embedding(text):
            return [1.0, 0.0]

        evaluator = AnswerRelevancy(embedding_fn=dummy_embedding, input_key="question")
        ctx = EvaluatorContext(
            input_data={"question": "What is Python?", "context": "some context"},
            output_data="Python is great",
        )
        result = evaluator.evaluate(ctx)
        assert result == 1.0

    def test_answer_relevancy_missing_input_key(self):
        """Test with missing input key."""

        def dummy_embedding(text):
            return [1.0, 0.0]

        evaluator = AnswerRelevancy(embedding_fn=dummy_embedding, input_key="missing_key")
        ctx = EvaluatorContext(
            input_data={"question": "What is Python?"},
            output_data="Python is great",
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_answer_relevancy_none_output(self):
        def dummy_embedding(text):
            return [1.0, 0.0]

        evaluator = AnswerRelevancy(embedding_fn=dummy_embedding)
        ctx = EvaluatorContext(
            input_data={"question": "test"},
            output_data=None,
        )
        result = evaluator.evaluate(ctx)
        assert result == 0.0

    def test_answer_relevancy_invalid_threshold(self):
        def dummy_embedding(text):
            return [1.0, 0.0]

        with pytest.raises(ValueError, match="threshold must be between 0 and 1"):
            AnswerRelevancy(embedding_fn=dummy_embedding, threshold=-0.1)

    @pytest.mark.asyncio
    async def test_answer_relevancy_async(self):
        """Test async evaluation."""

        async def async_embedding(text):
            return [1.0, 0.0, 0.0]

        evaluator = AnswerRelevancy(embedding_fn=async_embedding)
        ctx = EvaluatorContext(
            input_data={"question": "What is Python?"},
            output_data="Python is a programming language",
        )
        result = await evaluator.evaluate_async(ctx)
        assert result == 1.0
