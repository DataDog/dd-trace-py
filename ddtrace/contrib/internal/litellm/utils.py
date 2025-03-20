# TODO: temporary since we may want to intercept get_llm_provider response
def get_provider(model):
    parsed_model = model.split("/")
    if len(parsed_model) == 2:
        return parsed_model[0]
    else:
        return ""

def tag_request(span, kwargs):
    if "metadata" in kwargs and "headers" in kwargs["metadata"] and "host" in kwargs["metadata"]["headers"]:
        span.set_tag_str("litellm.host", kwargs["metadata"]["headers"]["host"])
        

