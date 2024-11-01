def get_system_instruction_parts(instance):
    """
    Assumes that the system instruction is provided as []Part
    """
    return getattr(instance, "_system_instruction", None)

def get_generation_config_dict(instance, kwargs):
    """
    The generation config can be defined on the model instance or 
    as a kwarg to generate_content. Therefore, try to extract this information
    from the kwargs and otherwise default to checking the model instance attribute.
    """
    generation_config_arg = kwargs.get("generation_config", {})
    if generation_config_arg != {}:
        return generation_config_arg if isinstance(generation_config_arg, dict) else generation_config_arg.to_dict()
    generation_config_attr = getattr(instance, "_generation_config", {})
    return generation_config_attr if isinstance(generation_config_attr, dict) else generation_config_attr.to_dict()