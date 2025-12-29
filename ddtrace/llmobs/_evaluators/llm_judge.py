"""LLM-based evaluator for judging task outputs."""

import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext


logger = get_logger(__name__)


class LLMJudge(BaseEvaluator):
    """LLM-based evaluator that uses a language model to judge task outputs.

    This evaluator sends the input, output, and expected output to an LLM
    with a custom system prompt to get a structured evaluation response.

    Example::

        judge = LLMJudge(
            system_prompt="You are a grader. Evaluate the quality of the answer.",
            model_config={"model": "gpt-4o", "temperature": 0.0}
        )

        # Use in an experiment
        LLMObs.experiment(
            name="docs_qa_test",
            dataset=dataset,
            evaluators=[judge, other_evaluator]
        )

    :param system_prompt: The system prompt to guide the LLM judge
    :param model_config: Configuration for the LLM model (e.g., model name, temperature)
    :param name: Optional custom name for the evaluator
    """

    def __init__(
        self,
        system_prompt: str = "You are an evaluator. Analyze the task output and provide a score.",
        model_config: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
    ):
        """Initialize the LLM Judge evaluator.

        :param system_prompt: The system prompt to guide the LLM judge
        :param model_config: Configuration dict containing model settings:
            - model: The model name (e.g., "gpt-4o", "claude-3-sonnet")
            - temperature: Temperature for sampling (default: 0.0)
            - max_tokens: Maximum tokens in response
            - Any other model-specific parameters
        :param name: Optional custom name for the evaluator
        """
        super().__init__(name=name or "llm_judge")
        if not system_prompt or not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        self.system_prompt = system_prompt
        self.model_config = model_config or {}

        # Validate model_config
        if not isinstance(self.model_config, dict):
            raise TypeError("model_config must be a dictionary")

        # Set defaults
        if "model" not in self.model_config:
            logger.warning("No model specified in model_config, using default")
        if "temperature" not in self.model_config:
            self.model_config["temperature"] = 0.0

    def _build_prompt(self, context: EvaluatorContext) -> str:
        """Build the evaluation prompt from the context.

        :param context: The evaluation context
        :return: The formatted prompt string
        """
        prompt_parts = [
            "Task Input:",
            json.dumps(context.input_data, indent=2),
            "\nTask Output:",
            json.dumps(context.output_data, indent=2) if context.output_data is not None else "None",
        ]

        if context.expected_output is not None:
            prompt_parts.extend(
                [
                    "\nExpected Output:",
                    json.dumps(context.expected_output, indent=2),
                ]
            )

        prompt_parts.append(
            "\nProvide your evaluation as a JSON object with a 'score' field (0-1) "
            "and an optional 'reasoning' field explaining your assessment."
        )

        return "\n".join(prompt_parts)

    def _call_llm(self, prompt: str) -> Dict[str, Any]:
        """Call the LLM with the given prompt.

        This is a placeholder that should be overridden or extended to
        actually call an LLM service.

        :param prompt: The prompt to send to the LLM
        :return: The parsed response from the LLM
        """
        # TODO: Implement actual LLM call using the configured model
        # This would integrate with OpenAI, Anthropic, or other providers
        # For now, return a placeholder response
        logger.warning(
            "LLMJudge._call_llm is not fully implemented. Override this method or integrate with an LLM provider."
        )
        return {
            "score": 0.5,
            "reasoning": "Placeholder evaluation - LLM integration not implemented",
        }

    async def _call_llm_async(self, prompt: str) -> Dict[str, Any]:
        """Call the LLM asynchronously with the given prompt.

        This is a placeholder that should be overridden to provide
        async LLM calls.

        :param prompt: The prompt to send to the LLM
        :return: The parsed response from the LLM
        """
        # TODO: Implement actual async LLM call
        # For now, fall back to sync version
        return self._call_llm(prompt)

    def evaluate(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform synchronous LLM-based evaluation.

        :param context: The evaluation context
        :return: Evaluation results including score and reasoning
        """
        try:
            prompt = self._build_prompt(context)
            result = self._call_llm(prompt)

            # Validate the result
            if not isinstance(result, dict):
                raise ValueError("LLM response must be a dictionary")
            if "score" not in result:
                raise ValueError("LLM response must contain a 'score' field")

            return result
        except Exception as e:
            logger.error("Error during LLM evaluation: %s", str(e))
            return {
                "score": 0.0,
                "error": str(e),
                "reasoning": f"Evaluation failed: {str(e)}",
            }

    async def evaluate_async(self, context: EvaluatorContext) -> Dict[str, Any]:
        """Perform asynchronous LLM-based evaluation.

        :param context: The evaluation context
        :return: Evaluation results including score and reasoning
        """
        try:
            prompt = self._build_prompt(context)
            result = await self._call_llm_async(prompt)

            # Validate the result
            if not isinstance(result, dict):
                raise ValueError("LLM response must be a dictionary")
            if "score" not in result:
                raise ValueError("LLM response must contain a 'score' field")

            return result
        except Exception as e:
            logger.error("Error during async LLM evaluation: %s", str(e))
            return {
                "score": 0.0,
                "error": str(e),
                "reasoning": f"Evaluation failed: {str(e)}",
            }
