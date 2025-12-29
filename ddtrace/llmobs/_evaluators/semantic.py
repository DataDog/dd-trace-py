"""Semantic similarity evaluators for LLMObs."""

from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext


logger = get_logger(__name__)


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
        # Returns: {"score": 0.92, "passed": True, "similarity": 0.92}

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

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform semantic similarity evaluation.

        :param context: The evaluation context
        :return: Dictionary with 'score', 'passed', 'similarity', and 'details'
        """
        output = context.output_data
        expected = context.expected_output

        # Handle None values
        if output is None and expected is None:
            return {
                "score": 1.0,
                "passed": True,
                "similarity": 1.0,
                "details": "Both values are None",
            }
        if output is None or expected is None:
            return {
                "score": 0.0,
                "passed": False,
                "similarity": 0.0,
                "details": f"One value is None: output={output}, expected={expected}",
            }

        # Convert to strings
        output_str = str(output)
        expected_str = str(expected)

        try:
            # Get embeddings
            output_embedding = self.embedding_fn(output_str)
            expected_embedding = self.embedding_fn(expected_str)

            # Calculate cosine similarity
            similarity = _cosine_similarity(output_embedding, expected_embedding)

            # Normalize to 0-1 range (cosine similarity is -1 to 1)
            normalized_similarity = (similarity + 1) / 2

            passed = normalized_similarity >= self.threshold

            return {
                "score": normalized_similarity,
                "passed": passed,
                "similarity": normalized_similarity,
                "details": {
                    "threshold": self.threshold,
                    "raw_cosine_similarity": similarity,
                },
            }
        except Exception as e:
            logger.error("Error calculating semantic similarity: %s", str(e))
            return {
                "score": 0.0,
                "passed": False,
                "similarity": 0.0,
                "details": f"Error: {str(e)}",
            }

    async def evaluate_async(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform async semantic similarity evaluation.

        If the embedding_fn is async, this will await it. Otherwise falls back to sync.

        :param context: The evaluation context
        :return: Dictionary with evaluation results
        """
        import inspect

        output = context.output_data
        expected = context.expected_output

        # Handle None values
        if output is None and expected is None:
            return {
                "score": 1.0,
                "passed": True,
                "similarity": 1.0,
                "details": "Both values are None",
            }
        if output is None or expected is None:
            return {
                "score": 0.0,
                "passed": False,
                "similarity": 0.0,
                "details": f"One value is None: output={output}, expected={expected}",
            }

        # Convert to strings
        output_str = str(output)
        expected_str = str(expected)

        try:
            # Get embeddings (async if supported)
            if inspect.iscoroutinefunction(self.embedding_fn):
                output_embedding = await self.embedding_fn(output_str)
                expected_embedding = await self.embedding_fn(expected_str)
            else:
                output_embedding = self.embedding_fn(output_str)
                expected_embedding = self.embedding_fn(expected_str)

            # Calculate cosine similarity
            similarity = _cosine_similarity(output_embedding, expected_embedding)

            # Normalize to 0-1 range
            normalized_similarity = (similarity + 1) / 2

            passed = normalized_similarity >= self.threshold

            return {
                "score": normalized_similarity,
                "passed": passed,
                "similarity": normalized_similarity,
                "details": {
                    "threshold": self.threshold,
                    "raw_cosine_similarity": similarity,
                },
            }
        except Exception as e:
            logger.error("Error calculating semantic similarity: %s", str(e))
            return {
                "score": 0.0,
                "passed": False,
                "similarity": 0.0,
                "details": f"Error: {str(e)}",
            }


class AnswerRelevancy(BaseEvaluator):
    """Evaluator that measures how well the output addresses the input.

    Measures the semantic relevance between the input question/prompt and
    the output answer. Useful for ensuring responses actually address what
    was asked, especially in RAG or Q&A systems.

    Unlike SemanticSimilarity which compares output to expected_output,
    this compares output to input_data to check if the answer is relevant
    to the question.

    Example::

        from openai import OpenAI
        client = OpenAI()

        def get_embedding(text):
            response = client.embeddings.create(
                input=text,
                model="text-embedding-3-small"
            )
            return response.data[0].embedding

        evaluator = AnswerRelevancy(
            embedding_fn=get_embedding,
            threshold=0.7
        )
        result = evaluator.evaluate(context)
        # Returns: {"score": 0.85, "passed": True, "relevancy": 0.85}

    :param embedding_fn: Function that takes text string and returns embedding vector (list of floats)
    :param threshold: Minimum relevancy score (0-1) required to pass (default: 0.7)
    :param input_key: Key to extract from input_data dict, or None to use entire input (default: None)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        embedding_fn: Callable[[str], List[float]],
        threshold: float = 0.7,
        input_key: Optional[str] = None,
        name: Optional[str] = None,
    ):
        """Initialize the AnswerRelevancy evaluator.

        :param embedding_fn: Function that converts text to embedding vector
        :param threshold: Minimum relevancy score (0-1) to pass
        :param input_key: Optional key to extract from input_data dict
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
        self.input_key = input_key

    def _extract_input_text(self, input_data: Dict[str, Any]) -> str:
        """Extract text from input_data based on input_key.

        :param input_data: The input data dictionary
        :return: Extracted text string
        """
        if self.input_key:
            if self.input_key not in input_data:
                raise KeyError(f"input_key '{self.input_key}' not found in input_data")
            return str(input_data[self.input_key])
        else:
            # Use entire input_data as string
            return str(input_data)

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform answer relevancy evaluation.

        :param context: The evaluation context
        :return: Dictionary with 'score', 'passed', 'relevancy', and 'details'
        """
        output = context.output_data
        input_data = context.input_data

        # Handle None output
        if output is None:
            return {
                "score": 0.0,
                "passed": False,
                "relevancy": 0.0,
                "details": "Output is None",
            }

        # Extract input text
        try:
            input_str = self._extract_input_text(input_data)
        except KeyError as e:
            return {
                "score": 0.0,
                "passed": False,
                "relevancy": 0.0,
                "details": str(e),
            }

        output_str = str(output)

        try:
            # Get embeddings
            input_embedding = self.embedding_fn(input_str)
            output_embedding = self.embedding_fn(output_str)

            # Calculate cosine similarity
            similarity = _cosine_similarity(input_embedding, output_embedding)

            # Normalize to 0-1 range
            normalized_relevancy = (similarity + 1) / 2

            passed = normalized_relevancy >= self.threshold

            return {
                "score": normalized_relevancy,
                "passed": passed,
                "relevancy": normalized_relevancy,
                "details": {
                    "threshold": self.threshold,
                    "raw_cosine_similarity": similarity,
                    "input_key": self.input_key,
                },
            }
        except Exception as e:
            logger.error("Error calculating answer relevancy: %s", str(e))
            return {
                "score": 0.0,
                "passed": False,
                "relevancy": 0.0,
                "details": f"Error: {str(e)}",
            }

    async def evaluate_async(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform async answer relevancy evaluation.

        :param context: The evaluation context
        :return: Dictionary with evaluation results
        """
        import inspect

        output = context.output_data
        input_data = context.input_data

        # Handle None output
        if output is None:
            return {
                "score": 0.0,
                "passed": False,
                "relevancy": 0.0,
                "details": "Output is None",
            }

        # Extract input text
        try:
            input_str = self._extract_input_text(input_data)
        except KeyError as e:
            return {
                "score": 0.0,
                "passed": False,
                "relevancy": 0.0,
                "details": str(e),
            }

        output_str = str(output)

        try:
            # Get embeddings (async if supported)
            if inspect.iscoroutinefunction(self.embedding_fn):
                input_embedding = await self.embedding_fn(input_str)
                output_embedding = await self.embedding_fn(output_str)
            else:
                input_embedding = self.embedding_fn(input_str)
                output_embedding = self.embedding_fn(output_str)

            # Calculate cosine similarity
            similarity = _cosine_similarity(input_embedding, output_embedding)

            # Normalize to 0-1 range
            normalized_relevancy = (similarity + 1) / 2

            passed = normalized_relevancy >= self.threshold

            return {
                "score": normalized_relevancy,
                "passed": passed,
                "relevancy": normalized_relevancy,
                "details": {
                    "threshold": self.threshold,
                    "raw_cosine_similarity": similarity,
                    "input_key": self.input_key,
                },
            }
        except Exception as e:
            logger.error("Error calculating answer relevancy: %s", str(e))
            return {
                "score": 0.0,
                "passed": False,
                "relevancy": 0.0,
                "details": f"Error: {str(e)}",
            }
