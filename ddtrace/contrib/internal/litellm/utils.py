# TODO: temporary since we may want to intercept get_llm_provider response
def get_provider(model, kwargs):
    if "custom_llm_provider" in kwargs:
        return kwargs["custom_llm_provider"]
    parsed_model = model.split("/")
    if len(parsed_model) == 2:
        return parsed_model[0]
    else:
        return ""
    
def tag_model_and_provider(litellm, span, requested_model):
    span.set_tag_str("litellm.request.model", requested_model)
    integration = litellm._datadog_integration
    provider = integration._provider_map.get(requested_model, None)
    if provider:
        span.set_tag_str("litellm.request.provider", provider)

def tag_request(span, kwargs):
    if "metadata" in kwargs and "headers" in kwargs["metadata"] and "host" in kwargs["metadata"]["headers"]:
        span.set_tag_str("litellm.host", kwargs["metadata"]["headers"]["host"])
        

