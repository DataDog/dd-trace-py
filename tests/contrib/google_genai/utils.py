from google.genai import types

from ddtrace.llmobs._integrations.google_genai import METADATA_PARAMS


FULL_GENERATE_CONTENT_CONFIG = types.GenerateContentConfig(
    temperature=0,
    top_p=0.95,
    top_k=20,
    candidate_count=1,
    seed=5,
    max_output_tokens=100,
    stop_sequences=["STOP!"],
    presence_penalty=0.0,
    frequency_penalty=0.0,
    system_instruction="You are a helpful assistant.",
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    safety_settings=[
        types.SafetySetting(
            category="HARM_CATEGORY_HATE_SPEECH",
            threshold="BLOCK_ONLY_HIGH",
        )
    ],
)

MOCK_GENERATE_CONTENT_RESPONSE = types.GenerateContentResponse(
    candidates=[
        types.Candidate(
            content=types.Content(
                role="model", parts=[types.Part.from_text(text="The sky is blue due to rayleigh scattering")]
            )
        )
    ],
    usage_metadata=types.GenerateContentResponseUsageMetadata(
        prompt_token_count=8, candidates_token_count=9, total_token_count=17
    ),
)

MOCK_GENERATE_CONTENT_RESPONSE_STREAM = [
    types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text="The sky")]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=8, total_token_count=8),
    ),
    types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=" is blue")]))
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=8, total_token_count=8),
    ),
    types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=" due to rayleigh scattering")])
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=8, candidates_token_count=9, total_token_count=17
        ),
    ),
]


# for testing tool calls
def get_current_weather(location: str, unit: str = "fahrenheit") -> dict:
    """Mock weather function for tool testing."""
    return {
        "location": location,
        "temperature": 72,
        "unit": unit,
        "forecast": "Sunny with light breeze",
    }


TOOL_GENERATE_CONTENT_CONFIG = types.GenerateContentConfig(
    tools=[get_current_weather],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    temperature=0,
)

MOCK_TOOL_CALL_RESPONSE = types.GenerateContentResponse(
    candidates=[
        types.Candidate(
            content=types.Content(
                role="model",
                parts=[types.Part.from_function_call(name="get_current_weather", args={"location": "Boston"})],
            )
        )
    ],
    usage_metadata=types.GenerateContentResponseUsageMetadata(
        prompt_token_count=10, candidates_token_count=5, total_token_count=15
    ),
)

MOCK_TOOL_FINAL_RESPONSE = types.GenerateContentResponse(
    candidates=[
        types.Candidate(
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(
                        text=(
                            "The weather in Boston is sunny with a light breeze and the temperature is "
                            "72 degrees Fahrenheit."
                        )
                    )
                ],
            )
        )
    ],
    usage_metadata=types.GenerateContentResponseUsageMetadata(
        prompt_token_count=25, candidates_token_count=20, total_token_count=45
    ),
)


def get_expected_metadata():
    metadata = {}
    for param in METADATA_PARAMS:
        metadata[param] = getattr(FULL_GENERATE_CONTENT_CONFIG, param, None)

    return metadata


def get_expected_tool_metadata():
    metadata = {}
    for param in METADATA_PARAMS:
        metadata[param] = getattr(TOOL_GENERATE_CONTENT_CONFIG, param, None)

    return metadata
