"""Instrumentation specific to crewai<1.0."""

import crewai

from ddtrace.contrib.internal.crewai.utils import traced_flow_kickoff
from ddtrace.contrib.internal.crewai.utils import traced_flow_method
from ddtrace.contrib.internal.crewai.utils import traced_kickoff
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap


def patch() -> None:
    wrap(crewai, "Crew.kickoff", traced_kickoff)
    wrap(crewai, "Flow.kickoff_async", traced_flow_kickoff)
    wrap(crewai, "Flow._execute_method", traced_flow_method)


def unpatch() -> None:
    unwrap(crewai.Crew, "kickoff")
    unwrap(crewai.Flow, "kickoff_async")
    unwrap(crewai.Flow, "_execute_method")
