from ddtrace.internal.utils import get_argument_value

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
    if not contents:
        return []
    if not contents:
        return []
    
    if not isinstance(contents, list):
        contents = [contents]
    
    normalized = []
    for content in contents:
        if hasattr(content, 'model_dump'):
            # pydantic model (ContentUnion)
            normalized.append(content.model_dump(exclude_none=True))
        elif isinstance(content, dict):
            # already a dict (ContentUnionDict)
            normalized.append(content)
        else:
            # fallback - convert to string representation
            normalized.append({"content": str(content)})
    
    return normalized

def tag_request(span, integration, instance, args, kwargs):
    contents = get_argument_value(args, kwargs, -1,  "contents")
    generation_config = get_argument_value(args, kwargs, -1, "config", optional=True)
    generation_config_dict = config_to_dict(generation_config)

    if generation_config_dict:
        for k, v in generation_config_dict.items():
            span.set_tag_str("google_genai.generation_config.%s" % k, str(v))

    # performance optimization????
    if not integration.is_pc_sampled_span(span):
        return

    # if contents:
    #     normalized_contents = normalize_contents(contents)
    #     for content_idx, content in enumerate(normalized_contents):
    #         _tag_request_content(span, integration, content, content_idx)


def tag_response(span, generations, integration, instance):
    for candidate_idx, candidate in enumerate(generations.candidates):
        if candidate.finish_reason:
            span.set_tag_str("google_genai.response.candidates.%d.finish_reason" % candidate_idx, candidate.finish_reason)
        if candidate.content.role:
            span.set_tag_str("google_genai.response.candidates.%d.content.role" % candidate_idx, str(candidate.content.role))
        # if candidate.token_count:
        #     span.set_tag_str("google_genai.response.candidates.%d.token_count" % candidate_idx, str(candidate.token_count))
        if not integration.is_pc_sampled_span(span):
            return
        
        for part_idx, part in enumerate(candidate.content.parts):
            tag_response_part_google("google_genai", span, integration, part, part_idx, candidate_idx)
    
    token_counts = generations.usage_metadata
    if not token_counts:
        return
    span.set_metric("google_genai.response.prompt_tokens", token_counts.prompt_token_count if token_counts.prompt_token_count else 0)
    span.set_metric("google_genai.response.total_tokens", token_counts.total_token_count if token_counts.total_token_count else 0)

