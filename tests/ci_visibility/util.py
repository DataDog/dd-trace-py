from contextlib import contextmanager

import ddtrace
from tests.utils import DummyCIVisibilityWriter


@contextmanager
def _patch_dummy_writer():
    original = ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter
    ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter = DummyCIVisibilityWriter
    yield
    ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter = original
