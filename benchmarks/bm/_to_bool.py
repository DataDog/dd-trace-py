def to_bool(val):
    """
    Copied from
    https://github.com/python-attrs/attrs/blob/e84b57ea687942e5344badfe41a2728e24037431/src/attr/converters.py#L114

    Please remove after updating attrs to 21.3.0.

    Convert "boolean" strings (e.g., from env. vars.) to real booleans.
    Values mapping to :code:`True`:
    - :code:`True`
    - :code:`"true"` / :code:`"t"`
    - :code:`"yes"` / :code:`"y"`
    - :code:`"on"`
    - :code:`"1"`
    - :code:`1`
    Values mapping to :code:`False`:
    - :code:`False`
    - :code:`"false"` / :code:`"f"`
    - :code:`"no"` / :code:`"n"`
    - :code:`"off"`
    - :code:`"0"`
    - :code:`0`
    :raises ValueError: for any other value.
    .. versionadded:: 21.3.0
    """
    if isinstance(val, str):
        val = val.lower()
    truthy = {True, "true", "t", "yes", "y", "on", "1", 1}
    falsy = {False, "false", "f", "no", "n", "off", "0", 0}
    try:
        if val in truthy:
            return True
        if val in falsy:
            return False
    except TypeError:
        # Raised when "val" is not hashable (e.g., lists)
        pass
    raise ValueError("Cannot convert value to bool: {}".format(val))
