"""Version-gated collection for the wrapping test suite.

Test files that use syntax introduced in a specific Python version would raise a
``SyntaxError`` *at import time* on older interpreters, before any ``skipif`` could
run. So we skip collecting them here via ``collect_ignore`` -- the same strategy as
``tests/appsec/iast/aspects/conftest.py`` (which hard-codes one file + version),
generalized below to a filename convention so new gated files need no edit here.

The ``test_*_py<major><minor>.py`` naming convention is made executable below: each
such file is parsed for its version suffix and ignored on anything older. Adding a
gated file therefore only needs the matching ruff exclude in pyproject.toml (which
cannot run Python logic), not an edit here.
"""

import os
import re
import sys

import pytest

from tests.wrapping.mechanisms import ALL_MECHANISMS


_HERE = os.path.dirname(__file__)
_VERSION_SUFFIX = re.compile(r"_py(\d)(\d+)\.py$")

collect_ignore = []
for _name in os.listdir(_HERE):
    _match = _VERSION_SUFFIX.search(_name)
    if _match and sys.version_info < (int(_match.group(1)), int(_match.group(2))):
        collect_ignore.append(_name)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "mechanism_specific: this test targets one wrapping mechanism by name (e.g. tracer.wrap() "
        "decorator-ordering) rather than the whole matrix; it is exempt from the `mech` guardrail.",
    )


def pytest_collection_modifyitems(items):
    """Guardrail: every matrix test must run over all wrapping mechanisms.

    A test that forgets the ``mech`` argument (and does not parametrize it via
    ``xfail_mechanism``) would silently run once instead of once per mechanism --
    a coverage hole the differential design exists to prevent. ``mech`` appears in
    ``fixturenames`` for both forms, so its absence means the test opted out by
    mistake. Fail collection loudly rather than under-test in silence.

    Tests marked ``mechanism_specific`` are genuinely about a single mechanism and
    opt out. This hook is registered globally, so it is also scoped to items under
    this directory -- otherwise a broader run (e.g. ``pytest tests/``) would abort
    on every test that legitimately has no ``mech``.
    """
    missing = [
        item.nodeid
        for item in items
        if str(getattr(item, "path", "")).startswith(_HERE + os.sep)
        and not any(item.iter_markers("mechanism_specific"))
        and "mech" not in item.fixturenames
    ]
    if missing:
        raise pytest.UsageError(
            "wrapping tests must take a `mech` argument (run over every mechanism); these do not:\n  "
            + "\n  ".join(missing)
        )


@pytest.fixture(params=list(ALL_MECHANISMS.values()), ids=list(ALL_MECHANISMS))
def mech(request):
    """The wrapping mechanism under test. Every test taking a ``mech`` argument is
    automatically run once per mechanism (internal_wrap, tracer_wrap, wrapt, wrapping_context).

    A test that is a known failure for a specific mechanism uses ``xfail_mechanism()``
    (see ``mechanisms.py``) instead of this fixture, which parametrizes ``mech`` itself.
    """
    return request.param
