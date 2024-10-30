def _extract_model_name(instance):
    """Extract the model name from the instance.
    The Vertex AI Python SDK stores model names in the format 
    `"publishers/google/models/{model_name}"` or `"projects/.../{model_name}"`
    so we do our best to return the model name instead of the full string.
    """
    model_name = getattr(instance, "model_name", "")
    if not model_name or not isinstance(model_name, str):
        return ""
    if "/" in model_name:
        return model_name.split("/")[-1]
    return model_name

def tag_request(span, integration, instance, args, kwargs):
    pass


def tag_response(span, generations, integration, instance):
    pass