from ddtrace.contrib.langgraph import get_version
from ddtrace.contrib.langgraph import patch
from ddtrace.contrib.langgraph import unpatch
from tests.contrib.patch import PatchTestCase


class TestLangGraphPatch(PatchTestCase.Base):
    __integration_name__ = "langgraph"
    __module_name__ = "langgraph"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langgraph):
        from langgraph.pregel import Pregel
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq

        self.assert_wrapped(RunnableSeq.invoke)
        self.assert_wrapped(RunnableSeq.ainvoke)
        self.assert_wrapped(Pregel.invoke)
        self.assert_wrapped(Pregel.ainvoke)
        self.assert_wrapped(PregelLoop.tick)

    def assert_not_module_patched(self, langgraph):
        from langgraph.pregel import Pregel
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq

        self.assert_not_wrapped(RunnableSeq.invoke)
        self.assert_not_wrapped(RunnableSeq.ainvoke)
        self.assert_not_wrapped(Pregel.invoke)
        self.assert_not_wrapped(Pregel.ainvoke)
        self.assert_not_wrapped(PregelLoop.tick)

    def assert_not_module_double_patched(self, langgraph):
        from langgraph.pregel import Pregel
        from langgraph.pregel.loop import PregelLoop
        from langgraph.utils.runnable import RunnableSeq

        self.assert_not_double_wrapped(RunnableSeq.invoke)
        self.assert_not_double_wrapped(RunnableSeq.ainvoke)
        self.assert_not_double_wrapped(Pregel.invoke)
        self.assert_not_double_wrapped(Pregel.ainvoke)
        self.assert_not_double_wrapped(PregelLoop.tick)
