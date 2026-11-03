from ddtrace.contrib.internal.crewai.patch import get_version
from ddtrace.contrib.internal.crewai.patch import patch
from ddtrace.contrib.internal.crewai.patch import unpatch
from tests.contrib.crewai.utils import CREWAI_VERSION
from tests.contrib.patch import PatchTestCase


def _crewai_1_7_targets(crewai):
    if CREWAI_VERSION < (1, 7, 0):
        return []
    return [
        crewai.Crew.akickoff,
        crewai.Task._aexecute_core,
        crewai.Agent.aexecute_task,
        crewai.tools.base_tool.Tool.run,
    ]


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
        for target in _crewai_1_7_targets(crewai):
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
        for target in _crewai_1_7_targets(crewai):
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
        for target in _crewai_1_7_targets(crewai):
            self.assert_not_double_wrapped(target)
