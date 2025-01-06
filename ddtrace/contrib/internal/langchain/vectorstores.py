import sys

import langchain


try:
    import langchain_community
except ImportError:
    langchain_community = None
try:
    import langchain_pinecone
except ImportError:
    langchain_pinecone = None

from ddtrace.contrib.internal.langchain.constants import API_KEY
from ddtrace.contrib.internal.langchain.utils import PATCH_LANGCHAIN_V0
from ddtrace.contrib.internal.langchain.utils import _extract_api_key
from ddtrace.contrib.internal.langchain.utils import _format_api_key
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.utils import get_argument_value


def _is_pinecone_vectorstore_instance(instance):
    """Safely check if a traced instance is a Pinecone VectorStore.
    langchain_community does not automatically import submodules which may result in AttributeErrors.
    """
    try:
        if not PATCH_LANGCHAIN_V0 and langchain_pinecone:
            return isinstance(instance, langchain_pinecone.PineconeVectorStore)
        if not PATCH_LANGCHAIN_V0 and langchain_community:
            return isinstance(instance, langchain_community.vectorstores.Pinecone)
        return isinstance(instance, langchain.vectorstores.Pinecone)
    except (AttributeError, ModuleNotFoundError, ImportError):
        return False


@with_traced_module
def traced_similarity_search(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    query = get_argument_value(args, kwargs, 0, "query")
    k = kwargs.get("k", args[1] if len(args) >= 2 else None)
    provider = instance.__class__.__name__.lower()
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="similarity_search",
        provider=provider,
        api_key=_extract_api_key(instance),
    )
    documents = []
    try:
        if integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.query", integration.trunc(query))
        if k is not None:
            span.set_tag_str("langchain.request.k", str(k))
        for kwarg_key, v in kwargs.items():
            span.set_tag_str("langchain.request.%s" % kwarg_key, str(v))
        if _is_pinecone_vectorstore_instance(instance) and hasattr(instance._index, "configuration"):
            span.set_tag_str(
                "langchain.request.pinecone.environment",
                instance._index.configuration.server_variables.get("environment", ""),
            )
            span.set_tag_str(
                "langchain.request.pinecone.index_name",
                instance._index.configuration.server_variables.get("index_name", ""),
            )
            span.set_tag_str(
                "langchain.request.pinecone.project_name",
                instance._index.configuration.server_variables.get("project_name", ""),
            )
            api_key = instance._index.configuration.api_key.get("ApiKeyAuth", "")
            span.set_tag_str(API_KEY, _format_api_key(api_key))  # override api_key for Pinecone
        documents = func(*args, **kwargs)
        span.set_metric("langchain.response.document_count", len(documents))
        for idx, document in enumerate(documents):
            span.set_tag_str(
                "langchain.response.document.%d.page_content" % idx, integration.trunc(str(document.page_content))
            )
            for kwarg_key, v in document.metadata.items():
                span.set_tag_str(
                    "langchain.response.document.%d.metadata.%s" % (idx, kwarg_key), integration.trunc(str(v))
                )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=documents, operation="retrieval")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "query": query,
                    "k": k or "",
                    "documents": [
                        {"page_content": document.page_content, "metadata": document.metadata} for document in documents
                    ],
                },
            )
    return documents
