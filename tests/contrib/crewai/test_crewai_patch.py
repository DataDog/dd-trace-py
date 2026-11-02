from ddtrace.contrib.internal.crewai.patch import get_version
from ddtrace.contrib.internal.crewai.patch import patch
from ddtrace.contrib.internal.crewai.patch import unpatch
from tests.contrib.patch import PatchTestCase


def _native_async_targets(crewai):
    """Methods only present in crewai>=1.0 (native async execution, Tool.run override)."""
    targets = []
    if "akickoff" in vars(crewai.Crew):
        targets.append(crewai.Crew.akickoff)
    if "_aexecute_core" in vars(crewai.Task):
        targets.append(crewai.Task._aexecute_core)
    if "aexecute_task" in vars(crewai.Agent):
        targets.append(crewai.Agent.aexecute_task)
    if "run" in vars(crewai.tools.base_tool.Tool):
        targets.append(crewai.tools.base_tool.Tool.run)
    return targets


class TestCrewAIPatch(PatchTestCase.Base):
    __integration_name__ = "crewai"
    __module_name__ = "crewai"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, crewai):
        self.assert_wrapped(crewai.Crew.kickoff)
        self.assert_wrapped(crewai.Crew._get_context)
        self.assert_wrapped(crewai.Task._execute_core)
        self.assert_wrapped(crewai.Task.execute_async)
        self.assert_wrapped(crewai.Agent.execute_task)
        self.assert_wrapped(crewai.tools.structured_tool.CrewStructuredTool.invoke)
        self.assert_wrapped(crewai.tools.structured_tool.CrewStructuredTool.ainvoke)
        self.assert_wrapped(crewai.tools.base_tool.BaseTool.run)
        for target in _native_async_targets(crewai):
            self.assert_wrapped(target)

    def assert_not_module_patched(self, crewai):
        self.assert_not_wrapped(crewai.Crew.kickoff)
        self.assert_not_wrapped(crewai.Crew._get_context)
        self.assert_not_wrapped(crewai.Task._execute_core)
        self.assert_not_wrapped(crewai.Task.execute_async)
        self.assert_not_wrapped(crewai.Agent.execute_task)
        self.assert_not_wrapped(crewai.tools.structured_tool.CrewStructuredTool.invoke)
        self.assert_not_wrapped(crewai.tools.structured_tool.CrewStructuredTool.ainvoke)
        self.assert_not_wrapped(crewai.tools.base_tool.BaseTool.run)
        for target in _native_async_targets(crewai):
            self.assert_not_wrapped(target)

    def assert_not_module_double_patched(self, crewai):
        self.assert_not_double_wrapped(crewai.Crew.kickoff)
        self.assert_not_double_wrapped(crewai.Crew._get_context)
        self.assert_not_double_wrapped(crewai.Task._execute_core)
        self.assert_not_double_wrapped(crewai.Task.execute_async)
        self.assert_not_double_wrapped(crewai.Agent.execute_task)
        self.assert_not_double_wrapped(crewai.tools.structured_tool.CrewStructuredTool.invoke)
        self.assert_not_double_wrapped(crewai.tools.structured_tool.CrewStructuredTool.ainvoke)
        self.assert_not_double_wrapped(crewai.tools.base_tool.BaseTool.run)
        for target in _native_async_targets(crewai):
            self.assert_not_double_wrapped(target)
