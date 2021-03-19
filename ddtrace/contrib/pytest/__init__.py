"""
The pytest integration traces test executions.

Enabling
~~~~~~~~

Enable traced execution of tests using ``pytest`` runner by
running ``pytest --ddtrace`` or by modifying any configuration
file read by pytest (``pytest.ini``, ``setup.cfg``, ...)::

    [pytest]
    ddtrace = 1

You can enable all integrations by using the ``--ddtrace-patch-all`` option or by adding this to your configuration::

    [pytest]
    ddtrace-patch-all = 1

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pytest["service"]

   The service name reported by default for pytest traces.

   This option can also be set with the ``DD_PYTEST_SERVICE`` environment
   variable.

   Default: ``"pytest"``


.. py:data:: ddtrace.config.pytest["operation_name"]

   The operation name reported by default for pytest traces.

   This option can also be set with the ``DD_PYTEST_OPERATION_NAME`` environment
   variable.

   Default: ``"pytest.test"``
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
