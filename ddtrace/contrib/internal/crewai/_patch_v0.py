"""Instrumentation specific to crewai<1.0."""

from typing import Any
from typing import Callable

import crewai

from ddtrace.contrib.internal.crewai.utils import trace_call
from ddtrace.contrib.internal.crewai.utils import trace_flow_kickoff
from ddtrace.contrib.internal.crewai.utils import trace_flow_method
from ddtrace.contrib.internal.crewai.utils import traced_task_get_context
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap


def traced_kickoff(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return trace_call(func, instance, args, kwargs, "crew")


def patch() -> None:
    wrap(crewai, "Crew.kickoff", traced_kickoff)
    wrap(crewai, "Crew._get_context", traced_task_get_context)
    wrap(crewai, "Flow.kickoff_async", trace_flow_kickoff)
    wrap(crewai, "Flow._execute_method", trace_flow_method)


def unpatch() -> None:
    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Crew, "_get_context")
    unwrap(crewai.Flow, "kickoff_async")
    unwrap(crewai.Flow, "_execute_method")
