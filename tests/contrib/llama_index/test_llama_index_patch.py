from ddtrace.contrib.internal.llama_index.patch import get_version
from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.internal.compat import is_wrapted
from tests.contrib.patch import PatchTestCase


class TestLlamaIndexPatch(PatchTestCase.Base):
    __integration_name__ = "llama_index"
    __module_name__ = "llama_index.core"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def _assert_ddtrace_wrapped(self, obj):
        """Assert that a method is wrapped by ddtrace on top of llama_index's own wrapping.

        llama_index uses wrapt internally to instrument its own methods, so all
        methods (query, aquery, retrieve, synthesize, run) are already FunctionWrapper
        instances before ddtrace touches them. When ddtrace patches, it wraps again,
        creating a double-wrapped chain:
          ddtrace FunctionWrapper -> llama_index FunctionWrapper -> original function

        This helper checks that the method is double-wrapped (ddtrace on top).
        """
        self.assertTrue(is_wrapted(obj), "{} is not wrapped at all".format(obj))
        self.assertTrue(
            hasattr(obj, "__wrapped__") and is_wrapted(obj.__wrapped__),
            "{} is not double-wrapped (ddtrace layer missing)".format(obj),
        )

    def _assert_not_ddtrace_wrapped(self, obj):
        """Assert that a method is NOT wrapped by ddtrace (only llama_index's own wrapping).

        The method should still be a wrapt wrapper (llama_index's own), but its
        __wrapped__ should be a plain function, not another wrapt wrapper.
        """
        # The method is always wrapped by llama_index, so is_wrapted is always True.
        # We check that there is no ddtrace layer on top by ensuring __wrapped__ is
        # not itself a wrapt wrapper.
        if hasattr(obj, "__wrapped__"):
            self.assertFalse(
                is_wrapted(obj.__wrapped__),
                "{} is still double-wrapped (ddtrace layer present)".format(obj),
            )

    def _assert_not_ddtrace_double_wrapped(self, obj):
        """Assert that ddtrace has not double-wrapped the method.

        When patched by ddtrace, the chain is:
          ddtrace FunctionWrapper -> llama_index FunctionWrapper -> function

        This checks that ddtrace hasn't added two layers:
          ddtrace FunctionWrapper -> ddtrace FunctionWrapper -> llama_index FunctionWrapper -> function
        """
        self._assert_ddtrace_wrapped(obj)
        # The inner wrapper (__wrapped__) should be llama_index's own wrapper,
        # whose __wrapped__ should be a plain function (not another wrapt wrapper).
        inner = obj.__wrapped__
        if hasattr(inner, "__wrapped__"):
            self.assertFalse(
                is_wrapted(inner.__wrapped__),
                "{} is triple-wrapped (ddtrace double-wrapped)".format(obj),
            )

    def assert_module_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.ingestion.pipeline import IngestionPipeline
        from llama_index.core.response_synthesizers.base import BaseSynthesizer

        self._assert_ddtrace_wrapped(BaseQueryEngine.query)
        self._assert_ddtrace_wrapped(BaseQueryEngine.aquery)
        self._assert_ddtrace_wrapped(BaseRetriever.retrieve)
        self._assert_ddtrace_wrapped(BaseSynthesizer.synthesize)
        self._assert_ddtrace_wrapped(IngestionPipeline.run)

    def assert_not_module_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.ingestion.pipeline import IngestionPipeline
        from llama_index.core.response_synthesizers.base import BaseSynthesizer

        self._assert_not_ddtrace_wrapped(BaseQueryEngine.query)
        self._assert_not_ddtrace_wrapped(BaseQueryEngine.aquery)
        self._assert_not_ddtrace_wrapped(BaseRetriever.retrieve)
        self._assert_not_ddtrace_wrapped(BaseSynthesizer.synthesize)
        self._assert_not_ddtrace_wrapped(IngestionPipeline.run)

    def assert_not_module_double_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.ingestion.pipeline import IngestionPipeline
        from llama_index.core.response_synthesizers.base import BaseSynthesizer

        self._assert_not_ddtrace_double_wrapped(BaseQueryEngine.query)
        self._assert_not_ddtrace_double_wrapped(BaseQueryEngine.aquery)
        self._assert_not_ddtrace_double_wrapped(BaseRetriever.retrieve)
        self._assert_not_ddtrace_double_wrapped(BaseSynthesizer.synthesize)
        self._assert_not_ddtrace_double_wrapped(IngestionPipeline.run)
