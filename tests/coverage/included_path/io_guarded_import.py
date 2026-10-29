RUNTIME_FALSE = bool(0)

if RUNTIME_FALSE:
    from tests.coverage.included_path.io_mod_edf import F  # noqa: F401

from tests.coverage.included_path.io_mod_xyz import Z  # noqa: E402


def io_guarded_caller():
    return Z
