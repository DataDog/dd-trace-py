import sys
from typing import Any
from typing import Dict
from typing import Optional

import langchain_community
import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.langchain_community.constants import text_embedding_models
from ddtrace.contrib.internal.langchain_community.constants import vectorstore_classes
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.llmobs._integrations import LangChainIntegration


langchain_openai = None
try:
    import langchain_openai
except ImportError:
    langchain_openai = None

langchain_pinecone = None
try:
    import langchain_pinecone
except ImportError:
    langchain_pinecone = None


config._add("langchain_community", {})


def get_version() -> str:
    return getattr(langchain_community, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"langchain_community": ">=0.0.38"}


def _extract_model_name(instance: Any) -> Optional[str]:
    """Extract model name or ID from llm instance."""
    for attr in ("model", "model_name", "model_id", "model_key", "repo_id"):
        if hasattr(instance, attr):
            return getattr(instance, attr)
    return None


@with_traced_module
def traced_embedding(langchain_community, pin, func, instance, args, kwargs):
    """
    This traces both embed_query(text) and embed_documents(texts), so we need to make sure
    we get the right arg/kwarg.
    """
    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    integration: LangChainIntegration = langchain_community._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider=provider,
        model=_extract_model_name(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    embeddings = None
    try:
        embeddings = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=embeddings, operation="embedding")
        span.finish()
    return embeddings


@with_traced_module
def traced_similarity_search(langchain_community, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain_community._datadog_integration
    provider = instance.__class__.__name__.lower()
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="similarity_search",
        provider=provider,
        instance=instance,
    )

    integration.record_instance(instance, span)

    documents = []
    try:
        documents = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=documents, operation="retrieval")
        span.finish()
    return documents


def patch():
    if getattr(langchain_community, "_datadog_patch", False):
        return

    langchain_community._datadog_patch = True
    integration = LangChainIntegration(integration_config=config.langchain_community)
    langchain_community._datadog_integration = integration

    Pin().onto(langchain_community)

    if langchain_openai:
        wrap(langchain_openai, "OpenAIEmbeddings.embed_documents", traced_embedding(langchain_community))

    if langchain_pinecone:
        wrap(langchain_pinecone, "PineconeVectorStore.similarity_search", traced_similarity_search(langchain_community))

    from langchain_community import embeddings  # noqa: F401
    from langchain_community import vectorstores  # noqa: F401

    for text_embedding_model in text_embedding_models:
        if hasattr(langchain_community.embeddings, text_embedding_model):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_query" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                wrap(
                    langchain_community.__name__,
                    "embeddings.%s.embed_query" % text_embedding_model,
                    traced_embedding(langchain_community),
                )
            if not isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_documents" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                wrap(
                    langchain_community.__name__,
                    "embeddings.%s.embed_documents" % text_embedding_model,
                    traced_embedding(langchain_community),
                )
    for vectorstore in vectorstore_classes:
        if hasattr(langchain_community.vectorstores, vectorstore):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(langchain_community.vectorstores, "%s.similarity_search" % vectorstore),
                wrapt.ObjectProxy,
            ):
                wrap(
                    langchain_community.__name__,
                    "vectorstores.%s.similarity_search" % vectorstore,
                    traced_similarity_search(langchain_community),
                )


def unpatch():
    if not getattr(langchain_community, "_datadog_patch", False):
        return

    langchain_community._datadog_patch = False

    if langchain_openai:
        unwrap(langchain_openai.OpenAIEmbeddings, "embed_documents")
    if langchain_pinecone:
        unwrap(langchain_pinecone.PineconeVectorStore, "similarity_search")

    for text_embedding_model in text_embedding_models:
        if hasattr(langchain_community.embeddings, text_embedding_model):
            if isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_query" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(langchain_community.embeddings, text_embedding_model), "embed_query")
            if isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_documents" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(langchain_community.embeddings, text_embedding_model), "embed_documents")
    for vectorstore in vectorstore_classes:
        if hasattr(langchain_community.vectorstores, vectorstore):
            if isinstance(
                deep_getattr(langchain_community.vectorstores, "%s.similarity_search" % vectorstore),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(langchain_community.vectorstores, vectorstore), "similarity_search")
