"""Experimental, not-yet-stable LLM Observability APIs.

Currently exposes the **inline experiments** API — a decorator-driven, trace-seeded local
regression mechanism ("unit test for LLM apps"): the ``experiment_start`` / ``experiment_end``
decorators plus the ``comparison`` evaluator helper. Mark an input->output boundary in your
already-instrumented app, then run it out-of-band with ``ddtrace-experiment run``, which
records a baseline and re-runs the current code against it, reporting a per-case
``match`` / ``changed`` verdict (offline by default; ``--publish`` sends the run to the
LLM Obs Experiments UI).

The decorators are an inert no-op during normal execution; they only activate under the
``ddtrace-experiment`` runner.

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
