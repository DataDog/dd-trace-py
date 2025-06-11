from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.utils import tag_response_part_google
from ddtrace.llmobs._utils import _get_attr

def extract_model_name_google_genai(model_name):
    if not model_name:
        return ""
    if "/" in model_name:
        return model_name.split("/")[-1]
    return model_name

def config_to_dict(config):
    # TODO: type checking
    if not config:
        return {}
    # config is a GenerationContentConfig or GenerationContentConfigDict which are 
    # subclasses of pydantic.BaseModel, so we can use model_dump to convert it to a dict
    return config.model_dump(exclude_none=True)

def normalize_contents(contents):
    """
    Contents has a complex union type structure:
    - contents: Union[ContentListUnion, ContentListUnionDict]
    - ContentListUnion = Union[list[ContentUnion], ContentUnion]  
    - ContentListUnionDict = Union[list[ContentUnionDict], ContentUnionDict]
    
    This function normalizes all these variants into a list of dicts
    """
    def extract_content(content):
        role = _get_attr(content, "role", None)
        parts = _get_attr(content, "parts", None)

        #if parts is missing and content itself is a PartUnion or list[PartUnion]
        if parts is None:
            if isinstance(content, list):
                parts = content
            else:
                parts = [content]

        elif not isinstance(parts, list):
            parts = [parts]

        return {"role": role, "parts": parts}

    if isinstance(contents, list):
        return [extract_content(c) for c in contents]
    return [extract_content(contents)]


def tag_request(span, integration, instance, args, kwargs):
    contents = get_argument_value(args, kwargs, -1,  "contents")
    generation_config = get_argument_value(args, kwargs, -1, "config", optional=True)
    generation_config_dict = config_to_dict(generation_config)

    for k, v in generation_config_dict.items():
        span.set_tag_str("google_genai.generation_config.%s" % k, str(v))

    # performance optimization????
    if not integration.is_pc_sampled_span(span):
        return
    
    #should I set tag for system instructions? they are already in the config.

    normalized_contents = normalize_contents(contents)
    for content_idx, content in enumerate(normalized_contents):
        _tag_request_content(span, integration, content, content_idx)

def _tag_request_content(span, integration, content, content_idx):
    role = _get_attr(content, "role", None)
    if role:
        span.set_tag_str("google_genai.request.contents.%d.role" % content_idx, str(role))
    parts = _get_attr(content, "parts", None)
    if parts:
        for part_idx, part in enumerate(parts):
            # see below for warning on tag_response_part_google
            tag_response_part_google("google_genai", span, integration, part, part_idx, content_idx)

def tag_response(span, generation_response, integration, instance):
    candidates = _get_attr(generation_response, "candidates", [])
    for candidate_idx, candidate in enumerate(candidates):
        if candidate.finish_reason:
            span.set_tag_str("google_genai.response.candidates.%d.finish_reason" % candidate_idx, candidate.finish_reason)
        if candidate.content and candidate.content.role:
            span.set_tag_str("google_genai.response.candidates.%d.content.role" % candidate_idx, str(candidate.content.role))
        # if candidate.token_count:
        #     span.set_tag_str("google_genai.response.candidates.%d.token_count" % candidate_idx, str(candidate.token_count))
        if not integration.is_pc_sampled_span(span):
            continue
        if candidate.content:
            parts = _get_attr(candidate.content, "parts", [])
            for part_idx, part in enumerate(parts):
                # using this for now, but this function checks for text and function call, whereas only one of these fields 
                # will be set at a time. also, should we check for the other fields?
                tag_response_part_google("google_genai", span, integration, part, part_idx, candidate_idx)
    
    token_counts = _get_attr(generation_response, "usage_metadata", None)
    if not token_counts:
        return
    span.set_metric("google_genai.response.prompt_tokens", _get_attr(token_counts, "prompt_token_count", 0))
    # no completion token count, substitute with candidate token count??? look above
    span.set_metric("google_genai.response.total_tokens", _get_attr(token_counts, "total_token_count", 0))

