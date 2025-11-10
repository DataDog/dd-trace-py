# Promptfoo local cursor provider.
# Parse the responses.yml file
import yaml
from pathlib import Path
from typing import Any


class ProviderOptions:
    id: str | None
    config: dict[str, Any] | None


class CallApiContextParams:
    vars: dict[str, str]


class TokenUsage:
    total: int
    prompt: int
    completion: int


class ProviderResponse:
    output: str | dict[str, Any] | None
    error: str | None
    tokenUsage: TokenUsage | None  # noqa: N815
    cost: float | None
    cached: bool | None
    logProbs: list[float] | None  # noqa: N815


class ProviderEmbeddingResponse:
    embedding: list[float]
    tokenUsage: TokenUsage | None  # noqa: N815
    cached: bool | None


class ProviderClassificationResponse:
    classification: dict[str, Any]
    tokenUsage: TokenUsage | None  # noqa: N815
    cached: bool | None


# Custom exceptions
class InvalidYAMLFormatError(ValueError):
    """Raised when YAML format is invalid."""


class ResponsesFileNotFoundError(OSError):
    """Raised when the responses file is not found."""


class ResponseParsingError(Exception):
    """Raised when there's an error parsing responses."""


def _raise_invalid_format_error() -> None:
    """Helper function to raise invalid format error."""
    raise InvalidYAMLFormatError(
        "Invalid YAML format - expected a list of prompt-output pairs " "or a 'responses' key containing the list"
    )


def _raise_invalid_list_error() -> None:
    """Helper function to raise invalid list error."""
    raise TypeError("Invalid YAML format - expected a list of prompt-output pairs")


def _raise_file_not_found_error(file_path: str) -> None:
    """Helper function to raise file not found error."""
    raise ResponsesFileNotFoundError(f"Error: {file_path} file not found")


def call_api(prompt: str, _options: dict[str, Any], _context: dict[str, Any]) -> ProviderResponse:
    result = {}
    try:
        result["output"] = parse_prompt_response(prompt)
    except Exception as e:
        result["error"] = "An error occurred during processing due to: " + str(e)
    return result


def parse_prompt_response(prompt: str) -> str:
    """Parse the logs/responses.yaml file to find a matching prompt and return its output.

    Args:
        prompt: The prompt string to search for

    Returns:
        The output string corresponding to the prompt, or a default message if not found

    """
    try:
        # Path to the responses YAML file
        yaml_file_path = Path("logs/responses.yaml")

        # Check if the file exists
        if not yaml_file_path.exists():
            _raise_file_not_found_error(str(yaml_file_path))

        # Load and parse the YAML file
        with yaml_file_path.open("r", encoding="utf-8") as file:
            yaml_data = yaml.safe_load(file)

        # Handle both formats: direct list or nested under 'responses' key
        if isinstance(yaml_data, dict) and "responses" in yaml_data:
            responses_data = yaml_data["responses"]
        elif isinstance(yaml_data, list):
            responses_data = yaml_data
        else:
            _raise_invalid_format_error()

        # Check if the responses data is a list
        if not isinstance(responses_data, list):
            _raise_invalid_list_error()

        # Search for the matching prompt
        for item in responses_data:
            if isinstance(item, dict) and "prompt" in item and "output" in item:
                # Check for exact match or if the prompt contains the search string
                if item["prompt"].strip() == prompt.strip() or prompt.strip() in item["prompt"]:
                    return item["output"]

        # If no match found, return a default message
        return f"No matching response found for prompt: '{prompt}'"

    except yaml.YAMLError as e:
        raise ResponseParsingError(f"Error parsing YAML file: {e!s}") from e
    except (ResponsesFileNotFoundError, InvalidYAMLFormatError, TypeError):
        raise
    except Exception as e:
        raise ResponseParsingError(f"Error reading responses file: {e!s}") from e
