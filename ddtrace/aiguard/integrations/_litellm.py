"""Subscriber of the litellm contrib call event, which evaluates LiteLLM SDK calls.
The guardrail of the LiteLLM proxy is in litellm.py.
"""

from typing import Any
from typing import Callable
from typing import ClassVar
from typing import Optional

from ddtrace.aiguard._api_client import AIGuardAbortError
from ddtrace.aiguard._api_client import AIGuardClient
from ddtrace.aiguard._api_client import Message
from ddtrace.aiguard._common import _get
from ddtrace.aiguard._common import evaluate_auto
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._context import is_aiguard_context_active
from ddtrace.aiguard._context import reset_aiguard_context_active
from ddtrace.aiguard._context import set_aiguard_context_active
from ddtrace.aiguard.integrations._openai_chat import _convert_openai_messages
from ddtrace.aiguard.integrations._openai_chat import _convert_openai_response
from ddtrace.contrib._events.litellm import LiteLLMCallEvent
from ddtrace.internal.core import ExecutionContext
from ddtrace.internal.core.subscriber import ContextSubscriber
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.utils import get_argument_value


logger = ddlogger.get_logger(__name__)


_CLAIM = "ai_guard_claim"


def _claim_call(ctx: ExecutionContext[LiteLLMCallEvent], kwargs: dict[str, Any]) -> bool:
    """Claim the call so the provider listeners skip it. Proxy requests (left to the guardrail), calls
    with fallbacks (each attempt, whose request a fallback can change, is a call) and calls under an
    active claim (of an outer call or another integration) get no claim.
    """
    if is_aiguard_context_active() or "proxy_server_request" in kwargs or kwargs.get("fallbacks"):
        return False
    ctx.set_item(_CLAIM, set_aiguard_context_active())
    return True


def _release_claim(ctx: ExecutionContext[LiteLLMCallEvent]) -> None:
    """Release only the claim of this call, kept on its context, so a nested litellm call never
    releases the claim of the outer one.
    """
    reset_aiguard_context_active(ctx.get_item(_CLAIM))
    ctx.set_item(_CLAIM, None)


def _evaluate(client: AIGuardClient, convert: Callable[..., list[Message]], *args: Any) -> None:
    """Evaluate the messages that convert returns. Only the bare AIGuardAbortError propagates, because
    litellm and its Router catch, wrap or retry every Exception; other errors fail open.
    """
    try:
        messages = convert(*args)
        if messages:
            evaluate_auto(client, messages, AI_GUARD.INTEGRATION_LITELLM)
    except AIGuardAbortError:
        raise
    except Exception:
        logger.debug("AI Guard litellm: failed to evaluate the call", exc_info=True)


def _chat_prompt(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Message]:
    messages = get_argument_value(args, kwargs, 1, "messages", optional=True)
    return _convert_openai_messages(messages) if isinstance(messages, (list, tuple)) else []


def _chat_prompt_to_evaluate(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Message]:
    # Like the OpenAI listener, evaluate a prompt that ends with a user or tool message.
    messages = _chat_prompt(args, kwargs)
    return messages if messages and messages[-1].get("role") in ("user", "tool") else []


def _chat_conversation(args: tuple[Any, ...], kwargs: dict[str, Any], resp: Any) -> list[Message]:
    answer = _convert_openai_response(resp)
    return _chat_prompt(args, kwargs) + answer if answer else []


def _text_prompt(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Message]:
    # Like litellm, make one user message for each prompt string. Token ID prompts have no text.
    prompt = get_argument_value(args, kwargs, 0, "prompt", optional=True)
    prompts = [prompt] if isinstance(prompt, str) else prompt if isinstance(prompt, (list, tuple)) else []
    return [Message(role="user", content=p) for p in prompts if isinstance(p, str)]


def _text_conversation(args: tuple[Any, ...], kwargs: dict[str, Any], resp: Any) -> list[Message]:
    answer = [Message(role="assistant", content=_get(choice, "text") or "") for choice in _get(resp, "choices") or []]
    return _text_prompt(args, kwargs) + answer if answer else []


_PROMPTS: dict[str, Callable[..., list[Message]]] = {
    "completion": _chat_prompt_to_evaluate,
    "acompletion": _chat_prompt_to_evaluate,
    "text_completion": _text_prompt,
    "atext_completion": _text_prompt,
}
_CONVERSATIONS: dict[str, Callable[..., list[Message]]] = {
    "completion": _chat_conversation,
    "acompletion": _chat_conversation,
    "text_completion": _text_conversation,
    "atext_completion": _text_conversation,
}


class LiteLLMCallSubscriber(ContextSubscriber[LiteLLMCallEvent]):
    """Evaluate the prompt when a LiteLLM SDK call starts, and the prompt with the answer when it ends.
    Streams end when the stream object is returned, so only their prompt is evaluated.
    """

    event_names = (LiteLLMCallEvent.event_name,)
    client: ClassVar[Optional[AIGuardClient]] = None

    @classmethod
    def on_started(cls, ctx: ExecutionContext[LiteLLMCallEvent]) -> None:
        event = ctx.event
        convert = _PROMPTS.get(event.operation)
        if cls.client is None or convert is None or not _claim_call(ctx, event.request_kwargs):
            return
        try:
            _evaluate(cls.client, convert, event.request_args, event.request_kwargs)
        except BaseException:
            # on_ended does not run when on_started raises.
            _release_claim(ctx)
            raise

    @classmethod
    def on_ended(cls, ctx: ExecutionContext[LiteLLMCallEvent], exc_info: Any) -> None:
        try:
            event = ctx.event
            convert = _CONVERSATIONS.get(event.operation)
            if cls.client and convert and ctx.get_item(_CLAIM) and exc_info[1] is None and not event.stream:
                _evaluate(cls.client, convert, event.request_args, event.request_kwargs, event.response)
        finally:
            _release_claim(ctx)
