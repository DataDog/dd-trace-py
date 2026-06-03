"""
The pytest-bdd integration traces executions of scenarios and steps.

Enabling
~~~~~~~~

Please follow the instructions for enabling `pytest` integration.

.. note::
   The ddtrace.pytest_bdd plugin for pytest-bdd has the side effect of importing
   the ddtrace package and starting a global tracer.

   If this is causing issues for your pytest-bdd runs where traced execution of
   tests is not enabled, you can deactivate the plugin::

     [pytest]
     addopts = -p no:ddtrace.pytest_bdd

   See the `pytest documentation
   <https://docs.pytest.org/en/7.1.x/how-to/plugins.html#deactivating-unregistering-a-plugin-by-name>`_
   for more details.

"""
