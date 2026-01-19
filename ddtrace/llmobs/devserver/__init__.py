"""
LLMObs Dev Server for remote evaluations.

This module provides a development server that exposes evaluators as HTTP endpoints,
allowing the Datadog Playground UI to trigger evaluations remotely.

Usage:
    from ddtrace.llmobs.devserver import evaluator

    @evaluator(name="accuracy", description="Check exact match")
    def accuracy_eval(input_data, output_data, expected_output):
        return 1 if output_data == expected_output else 0

    # With runtime config support
    @evaluator(name="similarity")
    def similarity_eval(input_data, output_data, expected_output, config=None):
        threshold = config.get("threshold", 0.8) if config else 0.8
        return compute_similarity(output_data, expected_output) >= threshold

Start the server via CLI:
    ddtrace-llmobs-serve evaluators.py --port 8080
"""
from ddtrace.llmobs.devserver.registry import EvaluatorRegistry
from ddtrace.llmobs.devserver.registry import evaluator


__all__ = ["evaluator", "EvaluatorRegistry"]
