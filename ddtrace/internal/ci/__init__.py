import uuid


__all__ = [
    "get_runtime_id",
]


def _generate_runtime_id():
    return uuid.uuid4().hex


_RUNTIME_ID = _generate_runtime_id()


def get_runtime_id():
    """Return a unique string identifier for this runtime.

    The runtime id is generated once and then remains constant.
    """
    return _RUNTIME_ID
