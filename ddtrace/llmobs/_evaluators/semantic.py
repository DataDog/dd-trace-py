"""Semantic similarity evaluators for LLMObs."""

from typing import Callable
from typing import List
from typing import Optional

from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult


def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors.

    :param vec1: First vector
    :param vec2: Second vector
    :return: Cosine similarity score between -1 and 1
    """
    if len(vec1) != len(vec2):
        raise ValueError(f"Vectors must have same length: {len(vec1)} != {len(vec2)}")

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    magnitude1 = sum(a * a for a in vec1) ** 0.5
    magnitude2 = sum(b * b for b in vec2) ** 0.5

    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0

    return dot_product / (magnitude1 * magnitude2)


class SemanticSimilarity(BaseEvaluator):
    """Evaluator that measures semantic similarity using embeddings.

    Compares the semantic meaning of output_data and expected_output using
    embedding vectors. Useful for evaluating open-ended responses where exact
    matches are too strict but semantic equivalence matters.

    The evaluator requires an embedding function that converts text to vectors.
    This can be OpenAI embeddings, sentence transformers, or any custom model.

    Example with OpenAI::

        from openai import OpenAI
        client = OpenAI()

        def get_embedding(text):
            response = client.embeddings.create(
                input=text,
                model="text-embedding-3-small"
            )
            return response.data[0].embedding

        evaluator = SemanticSimilarity(
            embedding_fn=get_embedding,
            threshold=0.8
        )
        result = evaluator.evaluate(context)
        # Returns: 0.92 (similarity score between 0.0 and 1.0)

    Example with sentence-transformers::

        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM-L6-v2')

        def get_embedding(text):
            return model.encode(text).tolist()

        evaluator = SemanticSimilarity(embedding_fn=get_embedding)

    :param embedding_fn: Function that takes text string and returns embedding vector (list of floats)
    :param threshold: Minimum similarity score (0-1) required to pass (default: 0.7)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        embedding_fn: Callable[[str], List[float]],
        threshold: float = 0.7,
        name: Optional[str] = None,
    ):
        """Initialize the SemanticSimilarity evaluator.

        :param embedding_fn: Function that converts text to embedding vector
        :param threshold: Minimum similarity score (0-1) to pass
        :param name: Optional custom name for the evaluator
        :raises ValueError: If threshold is not between 0 and 1
        """
        super().__init__(name=name)

        if not callable(embedding_fn):
            raise TypeError("embedding_fn must be a callable function")

        if not 0 <= threshold <= 1:
            raise ValueError(f"threshold must be between 0 and 1, got: {threshold}")

        self.embedding_fn = embedding_fn
        self.threshold = threshold

    def evaluate(self, context: EvaluatorContext) -> EvaluatorResult:
        """Perform semantic similarity evaluation.

        :param context: The evaluation context
        :return: EvaluatorResult with similarity score and pass/fail assessment based on threshold
        """
        output = context.output_data
        expected = context.expected_output

        if output is None and expected is None:
            return EvaluatorResult(value=1.0, assessment="pass")
        if output is None or expected is None:
            return EvaluatorResult(value=0.0, assessment="fail")

        output_str = str(output)
        expected_str = str(expected)

        output_embedding = self.embedding_fn(output_str)
        expected_embedding = self.embedding_fn(expected_str)

        similarity = _cosine_similarity(output_embedding, expected_embedding)
        normalized_similarity = (similarity + 1) / 2

        assessment = "pass" if normalized_similarity >= self.threshold else "fail"
        return EvaluatorResult(value=normalized_similarity, assessment=assessment)
