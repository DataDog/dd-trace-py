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


def get_expected_metadata():
    metadata = {}
    for param in METADATA_PARAMS:
        metadata[param] = getattr(FULL_GENERATE_CONTENT_CONFIG, param, None)

    return metadata
