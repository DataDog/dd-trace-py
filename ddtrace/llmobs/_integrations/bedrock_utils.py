
_MODEL_TYPE_IDENTIFIERS = (
    "foundation-model/",
    "custom-model/",
    "provisioned-model/",
    "imported-model/",
    "prompt/",
    "endpoint/",
    "inference-profile/",
    "default-prompt-router/",
)


def parse_model_id(model_id: str):
    """Best effort to extract the model provider and model name from the bedrock model ID.
    model_id can be a 1/2 period-separated string or a full AWS ARN, based on the following formats:
    1. Base model: "{model_provider}.{model_name}"
    2. Cross-region model: "{region}.{model_provider}.{model_name}"
    3. Other: Prefixed by AWS ARN "arn:aws{+region?}:bedrock:{region}:{account-id}:"
        a. Foundation model: ARN prefix + "foundation-model/{region?}.{model_provider}.{model_name}"
        b. Custom model: ARN prefix + "custom-model/{model_provider}.{model_name}"
        c. Provisioned model: ARN prefix + "provisioned-model/{model-id}"
        d. Imported model: ARN prefix + "imported-module/{model-id}"
        e. Prompt management: ARN prefix + "prompt/{prompt-id}"
        f. Sagemaker: ARN prefix + "endpoint/{model-id}"
        g. Inference profile: ARN prefix + "{application-?}inference-profile/{model-id}"
        h. Default prompt router: ARN prefix + "default-prompt-router/{prompt-id}"
    If model provider cannot be inferred from the model_id formatting, then default to "custom"
    """
    if not model_id.startswith("arn:aws"):
        model_meta = model_id.split(".")
        if len(model_meta) < 2:
            return "custom", model_meta[0]
        return model_meta[-2], model_meta[-1]
    for identifier in _MODEL_TYPE_IDENTIFIERS:
        if identifier not in model_id:
            continue
        model_id = model_id.rsplit(identifier, 1)[-1]
        if identifier in ("foundation-model/", "custom-model/"):
            model_meta = model_id.split(".")
            if len(model_meta) < 2:
                return "custom", model_id
            return model_meta[-2], model_meta[-1]
        return "custom", model_id
    return "custom", "custom"
