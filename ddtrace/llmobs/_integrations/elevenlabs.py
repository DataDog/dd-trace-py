from contextlib import contextmanager
from typing import Any
from typing import Iterator
from typing import Optional
from typing import cast

from ddtrace import config
from ddtrace.internal.native._native import Context
from ddtrace.llmobs._context import _DD_LLMOBS_CONTEXTVAR
from ddtrace.llmobs._integration_api import get_llmobs_service
from ddtrace.llmobs._integration_api import is_enabled
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.elevenlabs_agents import ConversationState
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_trace_id


class ElevenLabsIntegration(BaseLLMIntegration):
    _integration_name = "elevenlabs"

    def capture_context(self) -> Optional[Context]:
        # Use the import-safe service registry rather than importing the service
        # during integration initialization. Its snapshot survives parent finish.
        instance = getattr(get_llmobs_service(), "_instance", None)
        return cast(Optional[Context], instance._current_trace_context()) if instance is not None else None

    def trace(self, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Any) -> Any:
        if "llmobs_parent" not in kwargs:
            return super().trace(operation_id, submit_to_llmobs=submit_to_llmobs, **kwargs)
        # LLMObs activates every new LLM span even when APM activate=False. Scope
        # both its parent and activation to this call, preserving caller context.
        token = _DD_LLMOBS_CONTEXTVAR.set(kwargs.pop("llmobs_parent"))
        try:
            return super().trace(operation_id, submit_to_llmobs=submit_to_llmobs, **kwargs)
        finally:
            _DD_LLMOBS_CONTEXTVAR.reset(token)

    @contextmanager
    def activate_tool(self, span: Any, context_provider: Any) -> Iterator[None]:
        previous = context_provider.active()
        token = _DD_LLMOBS_CONTEXTVAR.set(span)
        try:
            context_provider.activate(span)
            yield
        finally:
            try:
                context_provider.activate(previous)
            finally:
                _DD_LLMOBS_CONTEXTVAR.reset(token)

    def _set_base_span_tags(self, span: Any, **kwargs: Any) -> None:
        span.set_tag("elevenlabs.agent_id", kwargs.get("agent_id", ""))

    def _llmobs_set_tags(
        self,
        span: Any,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        _annotate_llmobs_span_data(span, **kwargs)

    def annotate_voice_span(
        self,
        span: Any,
        kind: str,
        name: str,
        session_id: str,
        metadata: dict[str, Any],
        parent: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if parent is not None:
            kwargs["parent_id"] = str(parent.span_id)
            kwargs["trace_id"] = get_llmobs_trace_id(parent)
        if kind == "llm":
            # Context usage identifies a configured underlying model, not per-response
            # model attribution or token usage. ElevenLabs is the hosted agent provider.
            kwargs["model_provider"] = "elevenlabs"
        _annotate_llmobs_span_data(
            span,
            kind=kind,
            name=name,
            session_id=session_id,
            metadata=metadata,
            **kwargs,
        )


def create_conversation_state(instance: Any, parent: Any) -> Optional[ConversationState]:
    if not is_enabled():
        return None
    return ConversationState(ElevenLabsIntegration(config.elevenlabs), instance, parent)
