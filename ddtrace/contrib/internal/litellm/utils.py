def tag_request(span, kwargs):
    if "metadata" in kwargs and "headers" in kwargs["metadata"] and "host" in kwargs["metadata"]["headers"]:
        span.set_tag_str("litellm.request.host", kwargs["metadata"]["headers"]["host"])
