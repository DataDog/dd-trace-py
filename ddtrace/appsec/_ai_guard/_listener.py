from collections.abc import Mapping
from collections.abc import Sequence
from functools import partial
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.span import Span
from ddtrace.appsec._ai_guard._anthropic import _anthropic_messages_create_after
from ddtrace.appsec._ai_guard._anthropic import _anthropic_messages_create_before
from ddtrace.appsec._ai_guard._langchain import _langchain_chatmodel_generate_before
from ddtrace.appsec._ai_guard._langchain import _langchain_chatmodel_stream_before
from ddtrace.appsec._ai_guard._langchain import _langchain_generate_finally
from ddtrace.appsec._ai_guard._langchain import _langchain_llm_generate_before
from ddtrace.appsec._ai_guard._langchain import _langchain_llm_stream_before
from ddtrace.appsec._ai_guard._langchain import _langchain_patch
from ddtrace.appsec._ai_guard._langchain import _langchain_stream_started
from ddtrace.appsec._ai_guard._langchain import _langchain_unpatch
from ddtrace.appsec._ai_guard._openai_chat import _openai_chat_completion_after
from ddtrace.appsec._ai_guard._openai_chat import _openai_chat_completion_before
from ddtrace.appsec._ai_guard._openai_responses import _openai_response_create_after
from ddtrace.appsec._ai_guard._openai_responses import _openai_response_create_before
from ddtrace.appsec._ai_guard._streaming import BufferedAIGuardAsyncStream
from ddtrace.appsec._ai_guard._streaming import BufferedAIGuardStream
from ddtrace.appsec._ai_guard._streaming import _is_async_traced_stream
from ddtrace.appsec._ai_guard._streaming import _is_traced_stream
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import AIGuardClient
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.contrib.internal.trace_utils import _get_request_header_client_ip
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


def ai_guard_listen():
    client = new_ai_guard_client()
    _langchain_listen(client)
    _openai_listen(client)
    _anthropic_listen(client)
    core.on("set_http_meta_for_asm", _on_set_http_meta_for_ai_guard)


def _langchain_listen(client: AIGuardClient):
    core.on("langchain.patch", partial(_langchain_patch, client))
    core.on("langchain.unpatch", _langchain_unpatch)

    core.on("langchain.chatmodel.generate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.agenerate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.stream.before", partial(_langchain_chatmodel_stream_before, client))

    core.on("langchain.llm.generate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.agenerate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.stream.before", partial(_langchain_llm_stream_before, client))

    # AIDEV-NOTE: ``.stream.started`` is dispatched lazily from
    # ``BaseLangchainStreamHandler.start_stream`` (called by
    # ``TracedStream.__iter__`` / ``__aiter__`` on iteration entry), so a
    # stream created but never consumed cannot leak the counter into the
    # next call in the same task. The matching reset happens via
    # ``.stream.finally`` below (dispatched from ``finalize_stream``).
    core.on("langchain.chatmodel.stream.started", _langchain_stream_started)
    core.on("langchain.llm.stream.started", _langchain_stream_started)

    # AIDEV-NOTE: ``.finally`` listeners release the AI Guard active-context
    # counter. For non-streaming ``*.generate.*`` paths the counter is bumped
    # by the matching ``.before`` listener (``func(...)`` runs synchronously
    # so set + reset wrap the SDK call). For streaming the counter is bumped
    # by ``.stream.started`` above, and reset here once iteration ends. We
    # listen on ``.finally`` rather than ``.after`` so the reset still fires
    # when the underlying LLM call raises mid-iteration.
    core.on("langchain.chatmodel.generate.finally", _langchain_generate_finally)
    core.on("langchain.chatmodel.agenerate.finally", _langchain_generate_finally)
    core.on("langchain.llm.generate.finally", _langchain_generate_finally)
    core.on("langchain.llm.agenerate.finally", _langchain_generate_finally)
    core.on("langchain.chatmodel.stream.finally", _langchain_generate_finally)
    core.on("langchain.llm.stream.finally", _langchain_generate_finally)


def _openai_listen(client: AIGuardClient):
    core.on("openai.chat.completions.create.before", partial(_openai_chat_completion_before, client))
    core.on("openai.chat.completions.create.after", partial(_openai_chat_completion_after, client))
    core.on("openai.responses.create.before", partial(_openai_response_create_before, client))
    core.on("openai.responses.create.after", partial(_openai_response_create_after, client))


def _anthropic_listen(client: AIGuardClient):
    core.on("anthropic.messages.create.before", partial(_anthropic_messages_create_before, client))
    core.on("anthropic.messages.create.after", partial(_anthropic_messages_create_after, client))
    core.on("anthropic.patch", partial(_install_anthropic_wrappers, client))
    core.on("anthropic.unpatch", _uninstall_anthropic_wrappers)


_anthropic_wrappers_installed: bool = False


def _install_anthropic_wrappers(client: AIGuardClient) -> None:
    """Install outermost streaming buffer wrappers on all Anthropic message methods.

    Called when the Anthropic contrib fires ``anthropic.patch`` (at the end of
    its own ``patch()``), so our wrappers sit outermost in the chain.  The sync
    and async paths are separate because ``AsyncMessages.create`` is an
    ``async def`` — a sync wrapper would capture a coroutine object and never
    await the contrib's result.

    Wrappers are only installed when
    ``DD_AI_GUARD_ANALYZE_STREAM_RESPONSES_ENABLED=true`` so there is zero
    overhead on the default (flag-off) code path.
    """
    global _anthropic_wrappers_installed
    if not ai_guard_config._ai_guard_analyze_stream_responses_enabled:
        return

    import anthropic

    from ddtrace.appsec._ai_guard._anthropic_streaming import reconstruct_anthropic
    from ddtrace.contrib.internal.trace_utils import wrap
    from ddtrace.internal.utils.version import parse_version

    def sync_wrapper(func, instance, args, kwargs):
        result = func(*args, **kwargs)
        if not _is_traced_stream(result):
            return result

        def evaluate(resp):
            return _anthropic_messages_create_after(client, kwargs, resp)

        if _is_async_traced_stream(result):
            return BufferedAIGuardAsyncStream(result, reconstruct=reconstruct_anthropic, evaluate=evaluate)
        return BufferedAIGuardStream(result, reconstruct=reconstruct_anthropic, evaluate=evaluate)

    async def async_wrapper(func, instance, args, kwargs):
        result = await func(*args, **kwargs)
        if not _is_traced_stream(result):
            return result

        def evaluate(resp):
            return _anthropic_messages_create_after(client, kwargs, resp)

        return BufferedAIGuardAsyncStream(result, reconstruct=reconstruct_anthropic, evaluate=evaluate)

    # Mark before try so _uninstall_anthropic_wrappers can clean up partial installs on failure.
    _anthropic_wrappers_installed = True
    # AIDEV-NOTE: this wrap-target list MUST stay in sync with the contrib's own
    # wrap() calls in ddtrace/contrib/internal/anthropic/patch.py::patch() (and
    # the unwrap list in _uninstall_anthropic_wrappers below). If the contrib
    # adds/renames a streaming target or changes the >= (0, 37) beta gate, that
    # surface silently goes unbuffered here -- a security gap with no failing
    # test. Keep all three lists aligned.
    try:
        wrap("anthropic", "resources.messages.Messages.create", sync_wrapper)
        wrap("anthropic", "resources.messages.Messages.stream", sync_wrapper)
        # AsyncMessages.stream is a sync method (returns a manager); the TracedStream it
        # produces has an AsyncStreamHandler, so sync_wrapper dispatches to BufferedAIGuardAsyncStream.
        wrap("anthropic", "resources.messages.AsyncMessages.stream", sync_wrapper)
        wrap("anthropic", "resources.messages.AsyncMessages.create", async_wrapper)
        if parse_version(getattr(anthropic, "__version__", "0")) >= (0, 37):
            wrap("anthropic", "resources.beta.messages.messages.Messages.create", sync_wrapper)
            wrap("anthropic", "resources.beta.messages.messages.Messages.stream", sync_wrapper)
            wrap("anthropic", "resources.beta.messages.messages.AsyncMessages.stream", sync_wrapper)
            wrap("anthropic", "resources.beta.messages.messages.AsyncMessages.create", async_wrapper)
    except Exception:
        _uninstall_anthropic_wrappers()
        logger.debug("AI Guard anthropic: failed to install streaming wrappers", exc_info=True)


def _uninstall_anthropic_wrappers() -> None:
    """Remove AI Guard streaming buffer wrappers from Anthropic message methods.

    Called when the Anthropic contrib fires ``anthropic.unpatch`` (at the start
    of its own ``unpatch()``, before the contrib does its own unwrap calls).
    Peeling our outermost layer first lets the contrib's own ``unwrap`` calls
    correctly reach their inner wrappers.  No-ops if wrappers were never
    installed (e.g. the flag was off at patch time).
    """
    global _anthropic_wrappers_installed
    if not _anthropic_wrappers_installed:
        return
    _anthropic_wrappers_installed = False

    import anthropic

    from ddtrace.contrib.internal.trace_utils import unwrap
    from ddtrace.internal.utils.version import parse_version

    try:
        unwrap(anthropic.resources.messages.Messages, "create")
        unwrap(anthropic.resources.messages.Messages, "stream")
        unwrap(anthropic.resources.messages.AsyncMessages, "stream")
        unwrap(anthropic.resources.messages.AsyncMessages, "create")
        if parse_version(getattr(anthropic, "__version__", "0")) >= (0, 37):
            unwrap(anthropic.resources.beta.messages.messages.Messages, "create")
            unwrap(anthropic.resources.beta.messages.messages.Messages, "stream")
            unwrap(anthropic.resources.beta.messages.messages.AsyncMessages, "stream")
            unwrap(anthropic.resources.beta.messages.messages.AsyncMessages, "create")
    except Exception:
        logger.debug("AI Guard anthropic: failed to uninstall streaming wrappers", exc_info=True)


def _on_set_http_meta_for_ai_guard(
    span: Span,
    request_ip: Optional[str],
    raw_uri: Optional[str],
    route: Optional[str],
    method: Optional[str],
    request_headers: Optional[Mapping[str, str]],
    request_cookies: Optional[dict[str, str]],
    parsed_query: Optional[Mapping[str, Any]],
    request_path_params: Optional[Union[Mapping[str, Any], Sequence[Any]]],
    request_body: Any,
    status_code: Optional[Union[int, str]],
    response_headers: Optional[Mapping[str, str]],
    response_cookies: Optional[Mapping[str, str]],
    peer_ip: Optional[str] = None,
    headers_are_case_sensitive: bool = False,
) -> None:
    # Stash the candidate client IP so it can be applied to the service-entry span
    # only if an ai_guard span is actually created during the request. Restricted to
    # inbound server (WEB/SERVERLESS) spans so outbound HTTP client spans can't overwrite
    # the key with forwarded-IP headers from downstream calls.
    # https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6523551943
    if not ai_guard_config._ai_guard_enabled:
        return
    if span.span_type not in (SpanTypes.WEB, SpanTypes.SERVERLESS):
        return
    candidate_ip = _get_request_header_client_ip(request_headers, peer_ip, headers_are_case_sensitive) or peer_ip
    if candidate_ip:
        core.set_item(AI_GUARD.CLIENT_IP_CORE_KEY, candidate_ip)
