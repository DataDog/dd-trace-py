"""
The pytest integration traces test executions.

Enabling
~~~~~~~~

Enable traced execution of tests using ``pytest`` runner by
running ``pytest --ddtrace`` or by modifying any configuration
file read by pytest (``pytest.ini``, ``setup.cfg``, ...)::

    [pytest]
    ddtrace = 1


If you need to disable it, the option ``--no-ddtrace`` will take
precedence over ``--ddtrace`` and (``pytest.ini``, ``setup.cfg``, ...)

You can enable all integrations by using the ``--ddtrace-patch-all`` option
alongside ``--ddtrace`` or by adding this to your configuration::

    [pytest]
    ddtrace = 1
    ddtrace-patch-all = 1


.. note::
   The ddtrace plugin for pytest has the side effect of importing the ddtrace
   package and starting a global tracer.

   If this is causing issues for your pytest runs where traced execution of
   tests is not enabled, you can deactivate the plugin::

     [pytest]
     addopts = -p no:ddtrace

   See the `pytest documentation
   <https://docs.pytest.org/en/7.1.x/how-to/plugins.html#deactivating-unregistering-a-plugin-by-name>`_
   for more details.


Test automation exclusions
~~~~~~~~~~~~~~~~~~~~~~~~~~

Use pytest markers to exclude tests from Datadog automation::

    import pytest

    @pytest.mark.dd_prevent_failure_suppression
    @pytest.mark.dd_no_auto_retry
    def test_critical_behavior():
        assert check_critical_behavior()

``dd_prevent_failure_suppression`` overrides Datadog quarantine and disabling. It
does not disable retries or change pytest expected-failure behavior.
``dd_no_auto_retry`` excludes Datadog Auto Test Retries, Early Flake Detection,
and Attempt to Fix; explicit retries from pytest retry plugins remain available.
Both markers preserve developer skips, filters, and Test Impact Analysis selection.
Backend management assignments remain reported when their execution policy is overridden.

The markers follow normal pytest scoping: apply them to a function or class, set
``pytestmark`` at module scope, or mark an individual parameter case::

    @pytest.mark.parametrize("value", [
        pytest.param(1, marks=pytest.mark.dd_no_auto_retry),
        2,
    ])
    def test_parameter(value):
        assert check_value(value)

A marker on a parameterized function applies to every case. Aliases of the same
function share its markers; use separate test declarations or parameter markers
when different tests need different policies. The markers do not wrap or replace
test functions and can be combined with Hypothesis decorators.

With ``pytest-xdist``, a worker crash is retried only after that worker confirms its
exclusion declarations reached shared storage. An unrelated replacement worker
still collecting tests does not block retries. If the crashed worker could not
publish its exclusions, its crash remains final.

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: DD_SERVICE

   The service name reported by default for pytest traces.

   This option is the standard `DD_SERVICE` environment variable.

   Default: Name of the repository being tested, otherwise ``"test"`` if the repository name cannot be found.
"""
