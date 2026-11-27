from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from functools import partial
import json
from typing import Any
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._streaming import EnabledFn
from ddtrace.aiguard._streaming import EvaluateFn
from ddtrace.aiguard._streaming import ReconstructFn
from ddtrace.aiguard._streaming import _stream_analysis_enabled
from ddtrace.aiguard._streaming import buffered_events
from ddtrace.aiguard.integrations._anthropic import _anthropic_messages_create_after
from ddtrace.aiguard.integrations._anthropic import _anthropic_messages_create_before
from ddtrace.aiguard.integrations._anthropic_mcp import install_anthropic_mcp
from ddtrace.aiguard.integrations._anthropic_mcp import uninstall_anthropic_mcp
from ddtrace.aiguard.integrations._langchain import _langchain_chatmodel_generate_after
from ddtrace.aiguard.integrations._langchain import _langchain_chatmodel_generate_before
from ddtrace.aiguard.integrations._langchain import _langchain_chatmodel_stream_before
from ddtrace.aiguard.integrations._langchain import _langchain_generate_finally
from ddtrace.aiguard.integrations._langchain import _langchain_llm_generate_after
from ddtrace.aiguard.integrations._langchain import _langchain_llm_generate_before
from ddtrace.aiguard.integrations._langchain import _langchain_llm_stream_before
from ddtrace.aiguard.integrations._langchain import _langchain_patch
from ddtrace.aiguard.integrations._langchain import _langchain_stream_finally
from ddtrace.aiguard.integrations._langchain import _langchain_stream_started
from ddtrace.aiguard.integrations._langchain import _langchain_unpatch
from ddtrace.aiguard.integrations._openai_agents import _openai_agents_mcp_call_tool_before
from ddtrace.aiguard.integrations._openai_agents import _openai_agents_mcp_invoke_tool_before
from ddtrace.aiguard.integrations._openai_chat import _openai_chat_completion_after
from ddtrace.aiguard.integrations._openai_chat import _openai_chat_completion_before
from ddtrace.aiguard.integrations._openai_responses import _openai_response_create_after
from ddtrace.aiguard.integrations._openai_responses import _openai_response_create_before
from ddtrace.aiguard.integrations._openai_responses import may_request_mcp_approval
from ddtrace.contrib.internal._sdk_streams import ANTHROPIC_STREAM_CREATED
from ddtrace.contrib.internal._sdk_streams import OPENAI_STREAM_CREATED
from ddtrace.contrib.internal._sdk_streams import StreamEvents
from ddtrace.contrib.internal.trace_utils import _get_request_header_client_ip
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.aiguard import aiguard_config


logger = ddlogger.get_logger(__name__)

# Records the (owner_object, attr_name) pairs we actually wrapped, so uninstall
# only ever peels OUR layer -- never a contrib wrapper on a target we skipped or
# that failed mid-install.
_openai_wrapped_targets: list[tuple[Any, str]] = []


def ai_guard_listen() -> None:
    client = new_ai_guard_client()
    _langchain_listen(client)
    _openai_listen(client)
    _openai_agents_listen(client)
    _anthropic_listen(client)
    core.on("set_http_meta_for_asm", _on_set_http_meta_for_ai_guard)


def _langchain_listen(client: AIGuardClient) -> None:
    # Per-LLM kill switch (DD_AI_GUARD_LANGCHAIN_ENABLED, true by default). When set
    # to false, skip registering LangChain listeners so AI Guard never evaluates
    # LangChain calls, without affecting other providers or requiring a tracer rollback.
    if not aiguard_config._ai_guard_langchain_enabled:
        logger.debug("AI Guard LangChain auto-instrumentation disabled via DD_AI_GUARD_LANGCHAIN_ENABLED=false")
        return

    core.on("langchain.patch", partial(_langchain_patch, client))
    core.on("langchain.unpatch", _langchain_unpatch)

    core.on("langchain.chatmodel.generate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.agenerate.before", partial(_langchain_chatmodel_generate_before, client))
    core.on("langchain.chatmodel.stream.before", partial(_langchain_chatmodel_stream_before, client))

    core.on("langchain.llm.generate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.agenerate.before", partial(_langchain_llm_generate_before, client))
    core.on("langchain.llm.stream.before", partial(_langchain_llm_stream_before, client))

    # LangChain marks the AI Guard context active for the whole model call, which
    # makes the OpenAI / Anthropic listeners skip their own response evaluation.
    # These listeners are what replaces it -- without them a LangChain model
    # response reaches the caller unevaluated (APPSEC-70274). Streams have no after
    # event: AI Guard's stream wrapper evaluates them instead (_langchain_buffered_stream).
    core.on("langchain.chatmodel.generate.after", partial(_langchain_chatmodel_generate_after, client))
    core.on("langchain.chatmodel.agenerate.after", partial(_langchain_chatmodel_generate_after, client))
    core.on("langchain.llm.generate.after", partial(_langchain_llm_generate_after, client))
    core.on("langchain.llm.agenerate.after", partial(_langchain_llm_generate_after, client))

    # ``.stream.started`` is dispatched lazily from
    # ``BaseLangchainStreamHandler.start_stream`` (called by
    # ``TracedStream.__iter__`` / ``__aiter__`` on iteration entry), so a
    # stream created but never consumed cannot leak a claim into the
    # next call in the same task. The matching reset happens via
    # ``.stream.finally`` below (dispatched from ``finalize_stream``).
    core.on("langchain.chatmodel.stream.started", _langchain_stream_started)
    core.on("langchain.llm.stream.started", _langchain_stream_started)

    # The .finally listeners release the claim of the matching .before or .stream.started
    # listener; .finally rather than .after, so the release also runs when the call raises.
    core.on("langchain.chatmodel.generate.finally", _langchain_generate_finally)
    core.on("langchain.chatmodel.agenerate.finally", _langchain_generate_finally)
    core.on("langchain.llm.generate.finally", _langchain_generate_finally)
    core.on("langchain.llm.agenerate.finally", _langchain_generate_finally)
    core.on("langchain.chatmodel.stream.finally", _langchain_stream_finally)
    core.on("langchain.llm.stream.finally", _langchain_stream_finally)


def _openai_listen(client: AIGuardClient) -> None:
    # Per-LLM kill switch (DD_AI_GUARD_OPENAI_ENABLED, true by default). When set
    # to false, skip registering OpenAI listeners so AI Guard never evaluates
    # OpenAI calls, without affecting other providers or requiring a tracer rollback.
    if not aiguard_config._ai_guard_openai_enabled:
        logger.debug("AI Guard OpenAI auto-instrumentation disabled via DD_AI_GUARD_OPENAI_ENABLED=false")
        return
    core.on("openai.chat.completions.create.before", partial(_openai_chat_completion_before, client))
    core.on("openai.chat.completions.create.after", partial(_openai_chat_completion_after, client))
    core.on("openai.responses.create.before", partial(_openai_response_create_before, client))
    core.on("openai.responses.create.after", partial(_openai_response_create_after, client))
    core.on(OPENAI_STREAM_CREATED, partial(_openai_stream_created, client))
    core.on("openai.patch", partial(_install_openai_wrappers, client))
    core.on("openai.unpatch", _uninstall_openai_wrappers)


def _openai_agents_listen(client: AIGuardClient) -> None:
    # openai-agents MCP server tools run whatever the model provider, so only DD_AI_GUARD_MCP_ENABLED
    # gates them. Without listeners the contrib dispatches nothing, so disabled MCP costs no thread hop.
    if not aiguard_config._ai_guard_mcp_enabled:
        return
    core.on("openai_agents.mcp.invoke_tool.before", partial(_openai_agents_mcp_invoke_tool_before, client))
    core.on("openai_agents.mcp.call_tool.before", partial(_openai_agents_mcp_call_tool_before, client))


def _with_listed_tools(kwargs: dict[str, Any]) -> dict[str, Any]:
    """kwargs with an iterable tools argument turned into a list, so the SDK and every AI Guard hook
    see the same tools instead of an iterator the SDK consumes first.
    """
    tools = kwargs.get("tools")
    if tools is None or isinstance(tools, (list, tuple, str, bytes, Mapping)) or not isinstance(tools, Iterable):
        return kwargs
    return {**kwargs, "tools": list(tools)}


def _listed_tools_wrapper(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return func(*args, **_with_listed_tools(kwargs))


async def _async_listed_tools_wrapper(func: Callable[..., Any], instance: Any, args: Any, kwargs: Any) -> Any:
    return await func(*args, **_with_listed_tools(kwargs))


def _mcp_enabled() -> bool:
    return bool(aiguard_config._ai_guard_mcp_enabled)


def _install_openai_wrappers(client: AIGuardClient) -> None:
    """List iterable tools arguments on openai.patch, so the SDK and every AI Guard hook see the same tools."""
    analyze_streams = aiguard_config._ai_guard_analyze_stream_responses_enabled
    if not analyze_streams and not aiguard_config._ai_guard_mcp_enabled:
        return

    import openai

    from ddtrace.contrib.internal.trace_utils import wrap
    from ddtrace.internal.utils.formats import deep_getattr

    targets = [
        ("responses.Responses", "create", _listed_tools_wrapper),
        ("responses.AsyncResponses", "create", _async_listed_tools_wrapper),
        # parse consumes tools and posts directly, bypassing create.
        ("responses.Responses", "parse", _listed_tools_wrapper),
        ("responses.AsyncResponses", "parse", _async_listed_tools_wrapper),
    ]
    if analyze_streams:
        targets += [
            ("chat.Completions", "create", _listed_tools_wrapper),
            ("chat.AsyncCompletions", "create", _async_listed_tools_wrapper),
        ]
    for path, attr, wrapper in targets:
        owner = deep_getattr(openai.resources, path)
        if owner is None or not hasattr(owner, attr):
            continue
        try:
            wrap(owner, attr, wrapper)
        except Exception:
            logger.debug("AI Guard openai: failed to install tools wrapper on %s.%s", path, attr)
            continue
        _openai_wrapped_targets.append((owner, attr))


def _uninstall_openai_wrappers() -> None:
    """Unwrap the targets recorded in _openai_wrapped_targets, before the contrib's own unwrap calls."""
    from ddtrace.contrib.internal.trace_utils import unwrap

    while _openai_wrapped_targets:
        owner, attr = _openai_wrapped_targets.pop()
        try:
            unwrap(owner, attr)
        except Exception:
            logger.debug("AI Guard openai: failed to uninstall tools wrapper on %s.%s", owner, attr)


_OPENAI_CHAT = "chat"
_OPENAI_RESPONSES = "responses"
# Messages streams of the Anthropic API, Amazon Bedrock and Google Vertex AI clients; all send the Messages body.
_ANTHROPIC_STREAM_PATHS = {
    "/v1/messages": "messages",
    "/invoke-with-response-stream": "messages",
    ":streamRawPredict": "messages",
}


def _stream_request(events: StreamEvents, operations_by_path: dict[str, str]) -> Optional[tuple[str, dict[str, Any]]]:
    """Return the operation and the request the SDK sent for a new stream, or None when AI Guard skips it."""
    try:
        request = events.response.request
        path = request.url.path
        operation = next((op for suffix, op in operations_by_path.items() if path.endswith(suffix)), None)
        if operation is None:
            return None
        body = json.loads(request.content)
    except Exception:
        logger.debug("AI Guard: could not read the request of a stream", exc_info=True)
        return None
    return (operation, body) if isinstance(body, dict) else None


def _may_request_approval(kwargs: dict[str, Any]) -> bool:
    # A reusable prompt can configure hosted MCP tools without a tools argument; buffering it
    # only matters when a verdict can block, so monitor mode streams it untouched.
    try:
        return (bool(kwargs.get("prompt")) and aiguard_config._ai_guard_block) or may_request_mcp_approval(
            kwargs.get("tools")
        )
    except Exception:
        logger.debug("AI Guard openai: stream buffering decision failed, not buffering", exc_info=True)
        return False


def _openai_stream_created(client: AIGuardClient, events: StreamEvents) -> None:
    """Buffer a Chat Completions or Responses stream until AI Guard evaluates it.

    Every stream with stream analysis on; else, with MCP enabled, the Responses streams that can
    carry MCP approval requests.
    """
    analyze_streams = _stream_analysis_enabled()
    # Only stream analysis buffers chat streams; with it off, only MCP approvals can buffer Responses streams.
    operations_by_path = {"/responses": _OPENAI_RESPONSES} if analyze_streams or _mcp_enabled() else {}
    if analyze_streams:
        operations_by_path["/chat/completions"] = _OPENAI_CHAT
    if not operations_by_path:
        return
    request = _stream_request(events, operations_by_path)
    if request is None:
        return
    operation, kwargs = request
    enabled: EnabledFn = _stream_analysis_enabled
    if operation == _OPENAI_CHAT and analyze_streams:
        from ddtrace.contrib.internal.openai._streaming import reconstruct_openai_chat

        reconstruct: ReconstructFn = reconstruct_openai_chat
        evaluate: EvaluateFn = partial(_openai_chat_completion_after, client, kwargs)
    elif operation == _OPENAI_RESPONSES and (analyze_streams or (_mcp_enabled() and _may_request_approval(kwargs))):
        from ddtrace.contrib.internal.openai._streaming import reconstruct_openai_responses

        reconstruct = reconstruct_openai_responses
        if analyze_streams:
            evaluate = partial(_openai_response_create_after, client, kwargs)
        else:
            evaluate = partial(_openai_response_create_after, client, kwargs, approvals_only=True)
            enabled = _mcp_enabled
    else:
        return
    events.iterator = buffered_events(
        events.iterator, events.is_async, reconstruct=reconstruct, evaluate=evaluate, enabled=enabled
    )


def _anthropic_listen(client: AIGuardClient) -> None:
    # Per-LLM kill switch (DD_AI_GUARD_ANTHROPIC_ENABLED, true by default). When set
    # to false, skip registering Anthropic listeners so AI Guard never evaluates
    # Anthropic calls, without affecting other providers or requiring a tracer rollback.
    if not aiguard_config._ai_guard_anthropic_enabled:
        logger.debug("AI Guard Anthropic auto-instrumentation disabled via DD_AI_GUARD_ANTHROPIC_ENABLED=false")
        return

    core.on("anthropic.messages.create.before", partial(_anthropic_messages_create_before, client))
    core.on("anthropic.messages.create.after", partial(_anthropic_messages_create_after, client))
    core.on(ANTHROPIC_STREAM_CREATED, partial(_anthropic_stream_created, client))
    core.on("anthropic.patch", partial(install_anthropic_mcp, client))
    core.on("anthropic.unpatch", uninstall_anthropic_mcp)


def _anthropic_stream_created(client: AIGuardClient, events: StreamEvents) -> None:
    """Buffer a Messages stream until AI Guard evaluates it, when stream analysis is on."""
    if not _stream_analysis_enabled():
        return
    request = _stream_request(events, _ANTHROPIC_STREAM_PATHS)
    if request is None:
        return
    from ddtrace.aiguard.integrations._anthropic_streaming import reconstruct_anthropic

    events.iterator = buffered_events(
        events.iterator,
        events.is_async,
        reconstruct=reconstruct_anthropic,
        evaluate=partial(_anthropic_messages_create_after, client, request[1]),
    )


class _SpanTypeCarrier(Protocol):
    """Structural stand-in for ddtrace._trace.span.Span"""

    span_type: Optional[str]


def _on_set_http_meta_for_ai_guard(
    span: _SpanTypeCarrier,
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
    # Stash the client and peer IPs so they can be applied to the service-entry span
    # only if an ai_guard span is actually created during the request. Restricted to
    # inbound server (WEB/SERVERLESS) spans so outbound HTTP client spans can't overwrite
    # the key with forwarded-IP headers from downstream calls.
    # https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6523551943
    if not aiguard_config._ai_guard_enabled:
        return
    if span.span_type not in (SpanTypes.WEB, SpanTypes.SERVERLESS):
        return
    # Later metadata calls (e.g. mounted ASGI apps) can omit the peer captured earlier.
    if not peer_ip and core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY):
        return
    candidate_ip = _get_request_header_client_ip(request_headers, peer_ip, headers_are_case_sensitive) or peer_ip
    if candidate_ip:
        core.set_item(AI_GUARD.CLIENT_IP_CORE_KEY, (candidate_ip, peer_ip))
