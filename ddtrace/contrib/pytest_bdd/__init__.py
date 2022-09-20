"""
The pytest-bdd integration traces executions of scenarios and steps.

Enabling
~~~~~~~~

Please follow the instructions for enabling `pytest` integration.

.. note::
   The ddtrace.pytest_bdd plugin for pytest-bdd has the side effect of importing
   the ddtrace package and starting a global tracer.

   Generating traces can be avoided by setting ``DD_TRACE_ENABLED=False``.
   If other issues occur then the pytest-bdd plugin can be disabled entirely::

     [pytest]
     addopts = -p no:ddtrace -p no:ddtrace.pytest_bdd

   See the `pytest documentation
   <https://docs.pytest.org/en/7.1.x/how-to/plugins.html#deactivating-unregistering-a-plugin-by-name>`_
   for more details.

"""

from ddtrace import config


# pytest-bdd default settings
config._add(
    "pytest_bdd",
    dict(
        _default_service="pytest_bdd",
    ),
)
