# This example starts a devserver on :8787 with a simple capital-city experiment.
# It requires LLM Observability to be configured (DD_API_KEY, DD_APP_KEY, DD_SITE).
#
# Usage:
#
#   DD_API_KEY=... DD_APP_KEY=... DD_SITE=... python -m ddtrace.llmobs._examples.devserver_example
#
# Try:
#   curl http://localhost:8787/list
#   curl -X POST http://localhost:8787/eval -d '{"name":"capitals","stream":false}'
#   curl -X POST http://localhost:8787/eval -d '{"name":"capitals","stream":true}'
#   curl -X POST http://localhost:8787/eval -d '{"name":"capitals","stream":true,"config_override":{"accuracy":0.0}}'
#   curl -X POST http://localhost:8787/eval \
#     -d '{"name":"capitals","stream":false,"config_override":{"system_prompt":"Answer in a full sentence."}}'
import random

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._experiment import DatasetRecordInputType
from ddtrace.llmobs._experiment import JSONType


ANSWERS = {
    "France": "Paris",
    "Germany": "Berlin",
    "Japan": "Tokyo",
}


_DEFAULT_SYSTEM_PROMPT = "You are a geography expert. Answer with just the city name."


async def task(input_data: DatasetRecordInputType, config: dict) -> str:
    question = input_data["question"]
    cfg = config or {}
    accuracy = cfg.get("accuracy", 1.0)
    prefix = cfg.get("prefix", "")
    system_prompt = cfg.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)

    country_name = next((c for c in ANSWERS if c in question), None)
    answer = ANSWERS.get(country_name, "Unknown") if country_name else "Unknown"

    if accuracy < 1.0 and random.random() >= accuracy:
        answer = "Wrong answer"

    if "full sentence" in system_prompt:
        answer = f"The capital of {country_name or 'somewhere'} is {answer}."
    elif "just the city name" not in system_prompt:
        answer = f"[{system_prompt}] {answer}"

    return prefix + answer


def exact_match(input_data: DatasetRecordInputType, output_data: JSONType, expected_output: JSONType) -> bool:
    return output_data == expected_output


def similarity(input_data: DatasetRecordInputType, output_data: JSONType, expected_output: JSONType) -> float:
    if output_data == expected_output:
        return 1.0
    if isinstance(output_data, str) and isinstance(expected_output, str) and expected_output in output_data:
        return 0.5
    return 0.0


def quality_label(input_data: DatasetRecordInputType, output_data: JSONType, expected_output: JSONType) -> str:
    if output_data == expected_output:
        return "excellent"
    if isinstance(output_data, str) and isinstance(expected_output, str) and expected_output in output_data:
        return "good"
    return "poor"


def main() -> None:
    LLMObs.enable(
        ml_app="devserver-example",
        project_name="devserver-example",
    )

    # Pull the dataset if it already exists, otherwise create it.
    try:
        ds = LLMObs.pull_dataset("capitals", project_name="devserver-example")
    except Exception:
        ds = LLMObs.create_dataset(
            "capitals",
            project_name="devserver-example",
            records=[
                {
                    "input_data": {"question": "What is the capital of France?"},
                    "expected_output": "Paris",
                    "metadata": {},
                    "tags": [],
                },
                {
                    "input_data": {"question": "What is the capital of Germany?"},
                    "expected_output": "Berlin",
                    "metadata": {},
                    "tags": [],
                },
                {
                    "input_data": {"question": "What is the capital of Japan?"},
                    "expected_output": "Tokyo",
                    "metadata": {},
                    "tags": [],
                },
            ],
        )

    exp = LLMObs.async_experiment(
        name="capitals",
        description="Evaluate capital city question answering",
        project_name="devserver-example",
        task=task,
        dataset=ds,
        evaluators=[exact_match, similarity, quality_label],
        remote_config={
            "model": {
                "type": "string",
                "default": "gpt-3.5-turbo",
                "description": "LLM model to use",
                "choices": ["gpt-3.5-turbo", "gpt-4", "claude-3-sonnet"],
            },
            "system_prompt": {
                "type": "prompt",
                "default": _DEFAULT_SYSTEM_PROMPT,
                "description": "System prompt sent to the LLM",
            },
            "accuracy": {
                "type": "number",
                "default": 1.0,
                "description": "Simulated correctness rate",
                "min": 0.0,
                "max": 1.0,
            },
            "prefix": {
                "type": "string",
                "default": "",
                "description": "Text prepended to every answer",
            },
        },
        tags={"env": "dev"},
    )

    print("devserver listening on :8787")
    print()
    print("Try:")
    print("  curl http://localhost:8787/list")
    print('  curl -X POST http://localhost:8787/eval -d \'{"name":"capitals","stream":false}\'')
    print('  curl -X POST http://localhost:8787/eval -d \'{"name":"capitals","stream":true}\'')
    print(
        "  curl -X POST http://localhost:8787/eval"
        ' -d \'{"name":"capitals","stream":true,"config_override":{"accuracy":0.0}}\''
    )
    print(
        "  curl -X POST http://localhost:8787/eval"
        ' -d \'{"name":"capitals","stream":false,"config_override":{"system_prompt":"Answer in a full sentence."}}\''
    )
    print()

    LLMObs.devserver(experiments=[exp], port=8787)


if __name__ == "__main__":
    main()
