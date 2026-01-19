"""
Example evaluators for the LLMObs dev server.

Run with:
    ddtrace-llmobs-serve ddtrace/llmobs/devserver/examples/evaluators.py --port 8080

Then test with:
    curl -X POST http://localhost:8080/eval \
        -H "DD-API-KEY: 0123456789abcdef0123456789abcdef" \
        -H "Content-Type: application/json" \
        -d '{
            "evaluator_name": "accuracy",
            "input_data": {"question": "What is 2+2?"},
            "output_data": "4",
            "expected_output": "4"
        }'
"""
from ddtrace.llmobs.devserver import evaluator


@evaluator(name="accuracy", description="Check exact match between output and expected")
def accuracy_eval(input_data, output_data, expected_output):
    """
    Simple exact match evaluator.

    Returns:
        int: 1 if exact match, 0 otherwise
    """
    return 1 if output_data == expected_output else 0


@evaluator(name="contains_answer", description="Check if expected answer is contained in output")
def contains_answer(input_data, output_data, expected_output):
    """
    Check if the expected answer appears in the output.

    Returns:
        bool: True if expected is found in output
    """
    if isinstance(output_data, str) and isinstance(expected_output, str):
        return expected_output.lower() in output_data.lower()
    return output_data == expected_output


@evaluator(name="similarity", description="Score output similarity with configurable threshold")
def similarity_eval(input_data, output_data, expected_output, config=None):
    """
    Simple character-level similarity evaluator with configurable threshold.

    Config:
        threshold (float): Minimum similarity score to pass (default: 0.8)

    Returns:
        float: Similarity score between 0 and 1
    """
    threshold = config.get("threshold", 0.8) if config else 0.8

    if not isinstance(output_data, str) or not isinstance(expected_output, str):
        return 0.0

    # Simple character-level similarity (Jaccard)
    output_chars = set(output_data.lower())
    expected_chars = set(expected_output.lower())

    if not output_chars and not expected_chars:
        return 1.0
    if not output_chars or not expected_chars:
        return 0.0

    intersection = output_chars & expected_chars
    union = output_chars | expected_chars
    similarity = len(intersection) / len(union)

    return similarity


@evaluator(name="length_check", description="Check if output meets length requirements")
def length_check(input_data, output_data, expected_output, config=None):
    """
    Check if output meets minimum/maximum length requirements.

    Config:
        min_length (int): Minimum required length (default: 1)
        max_length (int): Maximum allowed length (default: 10000)

    Returns:
        str: "pass" or "fail"
    """
    min_len = config.get("min_length", 1) if config else 1
    max_len = config.get("max_length", 10000) if config else 10000

    if not isinstance(output_data, str):
        output_data = str(output_data)

    output_len = len(output_data)
    if min_len <= output_len <= max_len:
        return "pass"
    return "fail"
