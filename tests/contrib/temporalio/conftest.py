from collections.abc import Iterator

import pytest

from ddtrace.contrib.internal.temporalio.patch import patch
from ddtrace.contrib.internal.temporalio.patch import unpatch


@pytest.fixture(autouse=True)
def patch_temporalio() -> Iterator[None]:
    unpatch()
    patch()
    yield
    unpatch()
