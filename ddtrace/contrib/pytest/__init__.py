"""
Enable traced execution of tests using ``pytest`` runner by
running ``pytest --ddtrace`` or by modifying any configuration
file read by Pytest (``pytest.ini``, ``setup.cfg``, ...)::

    [pytest]
    ddtrace = 1

"""

from ddtrace import config

# pytest default settings
config._add("pytest", {})
