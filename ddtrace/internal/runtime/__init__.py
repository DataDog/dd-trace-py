import uuid

from .. import forksafe


__all__ = [
    "get_runtime_id",
]


def _generate_runtime_id():
    return uuid.uuid4().hex


_RUNTIME_ID = _generate_runtime_id()


@forksafe.register
def _set_runtime_id():
    global _RUNTIME_ID
    _RUNTIME_ID = _generate_runtime_id()


def get_runtime_id():
    """Return a unique string identifier for this runtime.

    Do not store this identifier as it can change when, e.g., the process forks.
    """
    return _RUNTIME_ID
