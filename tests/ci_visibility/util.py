from contextlib import contextmanager

import ddtrace
from ddtrace.internal.ci_visibility import DEFAULT_CI_VISIBILITY_SERVICE
from tests.utils import DummyCIVisibilityWriter


@contextmanager
def _patch_dummy_writer():
    original = ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter
    ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter = DummyCIVisibilityWriter
    yield
    ddtrace.internal.ci_visibility.recorder.CIVisibilityWriter = original


def _get_default_civisibility_ddconfig():
    new_ddconfig = ddtrace.settings.Config()
    new_ddconfig._add(
        "ci_visibility",
        {
            "_default_service": DEFAULT_CI_VISIBILITY_SERVICE,
        },
    )
    return new_ddconfig
