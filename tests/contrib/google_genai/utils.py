from google.genai import types



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
        prompt_token_count=8,
        candidates_token_count=9,
        total_token_count=17
    )
)
