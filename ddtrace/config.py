from .pin import Pin


def get_from(obj):
    """Retrieves the configuration for the given object.
    Any object that has an attached `Pin` must have a configuration
    and if a wrong object is given, an empty `dict` is returned
    for safety reasons.
    """
    pin = Pin.get_from(obj)
    if pin is None:
        return {}

    return pin._config
