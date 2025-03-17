# TODO: temporary since we may want to intercept get_llm_provider response
def get_model(kwargs):
    return kwargs.get("model", "")

# TODO: temporary since we may want to intercept get_llm_provider response
def get_provider(kwargs):
    request_model = kwargs.get("model", "")
    parsed_request_model = request_model.split("/")
    if len(parsed_request_model) == 2:
        return parsed_request_model[0]
    else:
        return ""
    
def tag_request(span, integration, kwargs):
    """Tag the completion span with request details.
    """
    messages = kwargs.get("messages", None)
    if messages:
        for i, message in enumerate(messages):
            content = message.get("content")
            role = message.get("role")
            span.set_tag_str("litellm.request.contents.%d.text" % i, str(content))
            span.set_tag_str("litellm.request.contents.%d.role" % i, str(role))

    tag_request_metadata(span, kwargs)

    stream = kwargs.get("stream", None)
    if stream:
        span.set_tag("litellm.request.stream", True)

    if not integration.is_pc_sampled_span(span):
        return

def tag_request_metadata(span, kwargs):
    temperature = kwargs.get("temperature", None)
    max_tokens = kwargs.get("max_tokens", None)
    if temperature:
        span.set_tag_str("litellm.request.metadata.temperature", str(temperature))
    if max_tokens:
        span.set_tag_str("litellm.request.metadata.max_tokens", str(max_tokens))
