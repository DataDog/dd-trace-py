
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

def tag_request(span, integration, instance, args, kwargs):
    model = extract_model_name_google_genai(get_argument_value(args, kwargs, -1, "model"))
    contents = get_argument_value(args, kwargs, -1,  "contents") #TODO: handle multiple contents
    config = config_to_dict(get_argument_value(args, kwargs, -1, "config", optional=True))
    


def tag_response(span, generations, integrations, instance):
    pass


