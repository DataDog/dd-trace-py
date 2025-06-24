def tag_request(span, instance):
    model = getattr(instance, "model", None)
    if model:
        span.set_tag("pydantic_ai.request.model", getattr(model, "model_name", ""))
        provider = getattr(model, "_provider", None)
        system = getattr(model, "system", None)
        # different model providers have different model classes and ways of accessing the provider name
        if provider or system:
            span.set_tag("pydantic_ai.request.provider", getattr(provider, "name", "") or system)