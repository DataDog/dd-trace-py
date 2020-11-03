"""
Enable traced execution of tests using ``pytest`` runner by
running ``pytest --ddtrace`` or by modifying any configuration
file read by Pytest (``pytest.ini``, ``setup.cfg``, ...)::

    [pytest]
    ddtrace = 1

"""

from ddtrace import config

from ...utils.formats import get_env

# pytest default settings
config._add(
    "pytest",
    dict(
        _default_service="pytest",
        operation_name=get_env("pytest", "operation_name", default="pytest.test"),
    ),
)
