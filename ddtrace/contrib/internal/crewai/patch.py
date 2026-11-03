import crewai

from ddtrace import config
from ddtrace.contrib.internal.crewai import _patch_v0
from ddtrace.contrib.internal.crewai import _patch_v1
from ddtrace.contrib.internal.crewai.utils import CREWAI_VERSION
from ddtrace.contrib.internal.crewai.utils import patched_find_triggered_methods
from ddtrace.contrib.internal.crewai.utils import traced_agent_execute
from ddtrace.contrib.internal.crewai.utils import traced_task_execute
from ddtrace.contrib.internal.crewai.utils import traced_task_execute_async
from ddtrace.contrib.internal.crewai.utils import traced_task_get_context
from ddtrace.contrib.internal.crewai.utils import traced_tool_acall
from ddtrace.contrib.internal.crewai.utils import traced_tool_call
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration


def get_version() -> str:
    return getattr(crewai, "__version__", "")


config._add("crewai", {})

# Targets whose wrappers differ between crewai majors, and targets that only exist in 1.x, are patched by the
# module for the installed major; the rest are patched below for every version.
_version_patch = _patch_v1 if CREWAI_VERSION >= (1, 0, 0) else _patch_v0


def _supported_versions() -> dict[str, str]:
    return {"crewai": ">=0.102"}


def patch():
    if getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = True

    integration: CrewAIIntegration = CrewAIIntegration(integration_config=config.crewai)
    crewai._datadog_integration = integration

    wrap(crewai, "Task.execute_async", traced_task_execute_async)
    wrap(crewai, "Agent.execute_task", traced_agent_execute)
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.invoke", traced_tool_call)
    wrap(crewai.tools.structured_tool, "CrewStructuredTool.ainvoke", traced_tool_acall)
    wrap(crewai.tools.base_tool, "BaseTool.run", traced_tool_call)
    wrap(crewai, "Task._execute_core", traced_task_execute)
    wrap(crewai, "Crew._get_context", traced_task_get_context)
    wrap(crewai, "Flow._find_triggered_methods", patched_find_triggered_methods)
    _version_patch.patch()


def unpatch():
    if not getattr(crewai, "_datadog_patch", False):
        return

    crewai._datadog_patch = False

    unwrap(crewai.Task, "execute_async")
    unwrap(crewai.Agent, "execute_task")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "invoke")
    unwrap(crewai.tools.structured_tool.CrewStructuredTool, "ainvoke")
    unwrap(crewai.tools.base_tool.BaseTool, "run")
    unwrap(crewai.Task, "_execute_core")
    # unwrap() reads the attribute through the class, which would drop the staticmethod of crewai>=1.7.
    crewai.Crew._get_context = crewai.Crew.__dict__["_get_context"].__wrapped__
    unwrap(crewai.Flow, "_find_triggered_methods")
    _version_patch.unpatch()

    delattr(crewai, "_datadog_integration")
