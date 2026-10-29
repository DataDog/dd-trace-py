"""The import-only POC concern example, as a fixture.

io_mod_abc is an import-time dependency of this module (both "tests" get it through the
import-time closure); io_mod_xyz is imported inside both function bodies — the second
import is a sys.modules cache hit, which a statement-level import hook still sees;
io_mod_edf/io_mod_hjk are each imported by only one function.
"""

from tests.coverage.included_path.io_mod_abc import ABC_VALUE  # noqa: F401


def io_test_z():
    from tests.coverage.included_path.io_mod_edf import F
    from tests.coverage.included_path.io_mod_xyz import Z

    return Z == F


def io_test_x():
    from tests.coverage.included_path.io_mod_hjk import K
    from tests.coverage.included_path.io_mod_xyz import X

    return X == K
