from typing import Union  # noqa:F401


def asbool(value: Union[str, bool, None]) -> bool:
    """Convert the given String to a boolean object.

    Accepted values are `True` and `1`.
    """
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    return value.lower() in ("true", "1")
