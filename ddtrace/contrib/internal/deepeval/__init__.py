"""
The deepeval integration adds the results of `deepeval <https://deepeval.com/>`_
LLM evaluations to Datadog Test Optimization test spans.

deepeval runs on top of pytest (via ``assert_test`` / ``evaluate``, executed with
``deepeval test run`` or plain ``pytest``). When those eval tests are run under
Test Optimization, this integration records each evaluated metric's result on the
test span using the generic ``eval.*`` schema (mirroring the benchmark
convention), so results can be filtered and aggregated in the Test Optimization
Explorer. The test is marked with ``test.type=eval`` and, per metric, the
following are captured (when available):

- ``eval.<metric>.score`` / ``eval.<metric>.threshold`` (metrics)
- ``eval.<metric>.passed`` (metric) and ``eval.<metric>.status`` (``pass``/``fail``/``error``)
- ``eval.<metric>.model`` (evaluation model) and ``eval.<metric>.reason``
- ``eval.<metric>.cost`` and, when the framework exposes them, token counts

Enabling
~~~~~~~~

The deepeval integration activates automatically once Test Optimization is enabled
for the run, i.e. when pytest receives the ``--ddtrace`` option. This works whether
tests are run with pytest directly (``pytest --ddtrace``) or via the deepeval CLI
(``deepeval test run <file> --ddtrace``) -- ``deepeval test run`` executes pytest
under the hood and forwards ``--ddtrace``. ``--ddtrace`` may also be set via
``addopts`` in your pytest configuration. See the ``pytest`` integration for
enabling instructions.

"""
