"""Experimental, not-yet-stable LLM Observability APIs.

Currently exposes the **inline experiments** decorators — a decorator-driven, trace-seeded
local regression mechanism ("unit test for LLM apps"). Mark an input->output boundary in
your already-instrumented app, then run it out-of-band with the ``ddtrace-experiment``
command, which captures a baseline and replays the current code against it.

The decorators are an inert no-op during normal execution; they only activate under the
``ddtrace-experiment`` runner. See ``ddtrace/llmobs/_local_regression_experiments_design.md``.

.. warning::
   APIs in this module are experimental and may change or be removed without notice.

Example::

    from ddtrace.llmobs.experimental import experiment_start

    @experiment_start(name="my_agent", inputs=["message"], output=lambda ret: ret[0])
    async def run_agent(agent, deps, message, user_handle=""):
        return await agent.handle_message(deps, message)
"""

from ddtrace.llmobs._inline_experiment import experiment_end
from ddtrace.llmobs._inline_experiment import experiment_start
from ddtrace.llmobs._inline_experiment_runner import comparison


__all__ = ["experiment_start", "experiment_end", "comparison"]
