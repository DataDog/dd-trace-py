def quantize_key_values(key):
    """
    Used in the Django trace operation method, it ensures that if a dict
    with values is used, we removes the values from the span meta
    attributes. For example::

        >>> quantize_key_values({'key', 'value'})
        # returns ['key']
    """
    if isinstance(key, dict):
        return key.keys()

    return key
