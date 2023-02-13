import warnings

import pytest


def test_deprecated():
    from ddtrace.contrib.grpc import constants as grpc_constants

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")

        assert grpc_constants.GRPC_PORT_KEY

        (warn,) = warns
        assert issubclass(warn.category, DeprecationWarning)
        assert (
            "ddtrace.contrib.grpc.constants.GRPC_PORT_KEY is deprecated and will be removed in version '2.0.0'."
            " Use ddtrace.ext.net.TARGET_PORT instead." == str(warn.message)
        )


def test_not_deprecated():
    from ddtrace.contrib.grpc import constants as grpc_constants

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")

        assert grpc_constants.GRPC_HOST_KEY
        assert len(warns) == 0


def test_invalid():
    with pytest.raises(ImportError):
        from ddtrace.contrib.grpc.constants import INVALID_CONSTANT  # noqa
