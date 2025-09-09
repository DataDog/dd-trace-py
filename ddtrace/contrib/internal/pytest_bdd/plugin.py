from ddtrace import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    from ddtrace import config

    # pytest-bdd default settings
    config._add(
        "pytest_bdd",
        dict(
            _default_service="pytest_bdd",
        ),
    )
