import uuid

from .runtime_metrics import (
    RuntimeTags,
    RuntimeMetrics,
    RuntimeWorker,
)
from .. import forksafe


__all__ = [
    "RuntimeTags",
    "RuntimeMetrics",
    "RuntimeWorker",
    "get_runtime_id",
]


_RUNTIME_ID = None


def _generate_runtime_id():
    global _RUNTIME_ID
    _RUNTIME_ID = uuid.uuid4().hex


@forksafe.register(after_in_child=_generate_runtime_id)
def get_runtime_id():
    return _RUNTIME_ID


_generate_runtime_id()


get_runtime_id.__doc__ = """Return a unique string identifier for this runtime.

Do not store this identifier as it can change when, e.g., the process forks."""
