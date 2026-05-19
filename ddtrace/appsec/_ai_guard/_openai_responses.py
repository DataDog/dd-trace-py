"""AI Guard integration for the OpenAI Responses API.

Provides listener functions for ``openai.responses.create.before`` and
``openai.responses.create.after`` events dispatched from the OpenAI contrib
patching layer.

The Responses API uses a different request/response shape than Chat
Completions:

- Request: ``instructions`` (str, system-prompt-like) and ``input`` (str |
  list of typed items) replace the ``messages=[...]`` list. A reusable
  ``prompt={"id": ..., "version": ..., "variables": {...}}`` kwarg is also
  supported — the server resolves the template into messages, so the
  ``variables`` values are user-controlled input that must reach AI Guard.
- Response: ``resp.output`` is a list of items (``message``, ``function_call``,
  ``mcp_call``, ``reasoning``, ``mcp_list_tools``, …) instead of
  ``resp.choices[].message``.

The converters target the same AI Guard ``Message`` schema used for Chat
Completions, so a tenant's AI Guard rules apply uniformly across both APIs.
"""

from collections.abc import Mapping
from typing import Any
from typing import Optional

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec._ai_guard._openai import _get
from ddtrace.appsec._ai_guard._openai import _wrap_abort_error
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import AIGuardClient
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


# Responses-API content blocks that carry user-visible text. Restricting to
# this allowlist avoids picking up stray ``text`` fields on unrelated block
# shapes (e.g. an annotation block with a "text" attribute that isn't user
# content). The bare ``"text"`` alias is intentionally excluded — the
# Responses API uses ``input_text`` (request) / ``output_text`` (response)
# exclusively, so accepting bare ``"text"`` would broaden the surface
# without matching any documented producer.
_RESPONSE_TEXT_BLOCK_TYPES = ("input_text", "output_text")
# Refusal-style blocks carry their content under a ``refusal`` key.
_RESPONSE_REFUSAL_BLOCK_TYPES = ("refusal", "output_refusal")
# AIDEV-NOTE: Item types that must never reach the AI Guard evaluator.
# - ``reasoning``: internal chain-of-thought emitted by reasoning-capable
#   models; evaluating it would gate on the model's private deliberation
#   rather than the user-visible conversation, and could leak CoT into AI
#   Guard logs / cassettes.
# - ``mcp_list_tools``: metadata describing the MCP server's tool inventory
#   advertised back to the client at the start of a session — not a turn.
# Forward-incompatible types we don't recognise are skipped implicitly by the
# explicit if/elif in the converters; this constant covers the cases where
# we know the type but choose to drop it.
_RESPONSE_SKIPPED_ITEM_TYPES = ("reasoning", "mcp_list_tools")

_IMAGE_MARKER = "[image]"
_FILE_MARKER = "[file]"


def _extract_text_content(content: Any) -> Optional[str]:
    """Flatten a Responses API ``content`` value (str or list of parts) to text.

    The Responses API wraps content in typed parts (``input_text``,
    ``output_text``, ``refusal``, ``input_image``, ``input_file``). AI Guard
    only models text; image/file parts surface as the literal markers
    ``[image]`` / ``[file]`` so a user message containing only an attachment
    still produces non-empty content — without a marker, an image-only user
    message would be discarded by the last-role gate in the before-hook.

    Image/file URLs and ``file_id`` values are intentionally NOT echoed into
    the AI Guard payload: they can be long, may contain signed-URL secrets,
    and add no evaluable signal.
    """
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return None
    parts = []
    for part in content:
        if part is None:
            continue
        if isinstance(part, str):
            parts.append(part)
            continue
        part_type = _get(part, "type")
        if part_type in _RESPONSE_TEXT_BLOCK_TYPES:
            text = _get(part, "text")
            if text:
                parts.append(str(text))
        elif part_type in _RESPONSE_REFUSAL_BLOCK_TYPES:
            refusal = _get(part, "refusal")
            if refusal:
                parts.append(str(refusal))
        elif part_type == "input_image":
            parts.append(_IMAGE_MARKER)
        elif part_type == "input_file":
            parts.append(_FILE_MARKER)
    return "".join(parts) if parts else None


def _flatten_tool_output(output: Any) -> Optional[str]:
    """Flatten a tool / MCP ``output`` value to a single string for AI Guard.

    OpenAI surfaces tool outputs as ``str``, a list of typed text blocks, or
    (less commonly) an arbitrary JSON object. Strings and typed lists are
    handled by ``_extract_text_content``; non-list non-string values fall
    back to ``str()`` so dict/numeric results still reach the evaluator
    instead of being silently dropped. Returns ``None`` when ``output`` is
    ``None`` or a list/tuple that flattened to nothing.
    """
    if output is None:
        return None
    text = _extract_text_content(output)
    if text is not None:
        return text
    if isinstance(output, (list, tuple)):
        return None
    return str(output)


def _function_tool_call_from_item(item: Any) -> ToolCall:
    """Build a ToolCall from a Responses-API ``function_call`` /
    ``custom_tool_call`` item (input or output).

    ``custom_tool_call`` carries its arguments under ``input`` (free-form
    string) instead of the JSON ``arguments`` field; we fall through both
    keys, with a final ``"{}"`` default for tools that elide arguments
    entirely. Truthiness (not ``is not None``) is intentional — an explicit
    empty string is not valid JSON for the evaluator and is safer treated as
    "no arguments".
    """
    call_id = _get(item, "call_id") or _get(item, "id", "")
    return ToolCall(
        id=str(call_id),
        function=Function(
            name=_get(item, "name", "") or "",
            arguments=_get(item, "arguments") or _get(item, "input") or "{}",
        ),
    )


def _mcp_call_messages(item: Any) -> list[Message]:
    """Build the AI Guard message pair for an ``mcp_call`` item.

    MCP calls are server-mediated tool invocations whose call + result land in
    the same response item. AI Guard models conversations as discrete turns,
    so we split each ``mcp_call`` into two messages: an assistant turn issuing
    the call, then a ``role="tool"`` turn carrying the server's output. The
    result message is only emitted when the MCP server actually returned a
    body — an in-flight or failed call (no ``output``) surfaces as just the
    assistant tool_call.
    """
    call_id = str(_get(item, "id") or _get(item, "call_id", "") or "")
    assistant = Message(role="assistant")
    assistant["tool_calls"] = [
        ToolCall(
            id=call_id,
            function=Function(
                name=_get(item, "name", "") or "",
                arguments=_get(item, "arguments") or "{}",
            ),
        )
    ]
    messages = [assistant]
    output_text = _flatten_tool_output(_get(item, "output"))
    if output_text:
        result = Message(role="tool")
        result["tool_call_id"] = call_id
        result["content"] = output_text
        messages.append(result)
    return messages


def _flatten_prompt_variables(prompt: Any) -> Optional[str]:
    """Render ``prompt.variables`` values as evaluable text for AI Guard.

    The Responses API ``prompt={"id": ..., "variables": {...}}`` kwarg lets
    the server resolve a stored template against caller-supplied variables.
    We don't see the resolved template, but the variable values are
    user-controlled (e.g. ``{"question": "..."}``) and must reach AI Guard
    before the model. Each variable is rendered as ``key: value`` lines so a
    policy rule can match on the variable name and on the payload.

    Value handling:
      - ``str`` → as-is.
      - ``list`` / ``tuple`` → flattened via :func:`_extract_text_content`
        (typed content parts only).
      - ``Mapping`` (including ``dict``) → routed through
        :func:`_extract_text_content` as a single-part list so typed
        content-part dicts (``{"type": "input_text", ...}``) render. Any
        mapping shape not recognised by the typed-part allowlist (e.g.
        ``{"image_url": "https://signed-url..."}``, ``{"file_id": "file-abc"}``)
        is **dropped** rather than ``str()``'d — those payloads can carry
        signed URLs / opaque ids that must not leak into the AI Guard
        evaluation request, and they carry no evaluable signal anyway.
      - Other scalars (``int``, ``float``, ``bool``) → stringified; these
        are user input with no secret-leak surface and would otherwise
        silently bypass the evaluator.

    The outer ``variables`` container is accepted as any ``Mapping``, not
    just ``dict``, so Mapping-shaped SDK wrappers don't silently bypass AI
    Guard for prompt-template calls.
    """
    if prompt is None:
        return None
    variables = _get(prompt, "variables")
    if not variables or not isinstance(variables, Mapping):
        return None
    parts = []
    for key, value in variables.items():
        if value is None:
            continue
        text: Optional[str]
        if isinstance(value, str):
            text = value
        elif isinstance(value, (list, tuple)):
            text = _extract_text_content(value)
        elif isinstance(value, Mapping):
            text = _extract_text_content([value])
        else:
            text = str(value)
        if text:
            parts.append(f"{key}: {text}")
    return "\n".join(parts) if parts else None


def _convert_openai_response_input(instructions: Any, input_: Any, prompt: Any = None) -> list[Message]:
    """Convert ``instructions`` + ``input`` (+ optional ``prompt``) to AI Guard ``Message`` list.

    ``instructions`` becomes a leading system message when set.
    ``prompt.variables`` (if any) become a leading user message so reusable
    prompt-template calls — where ``input`` may be ``None`` — are still gated
    by the before-hook. ``input`` may be ``None``, a plain string (single user
    message), or a list of items:
      - role messages → straight-through with text content flattened
        (typed parts ``input_text`` / ``output_text`` / ``refusal`` / ``input_image`` / ``input_file``).
      - ``function_call`` items → assistant message with a single ``tool_calls`` entry.
      - ``custom_tool_call`` items → same shape as ``function_call`` but with
        arguments under ``input=`` instead of ``arguments=``.
      - ``function_call_output`` items → ``role="tool"`` with ``tool_call_id`` and content.
      - ``mcp_call`` items → assistant ``tool_calls`` turn AND a ``role="tool"`` follow-up
        carrying the server's output (see ``_mcp_call_messages``).
      - Items in ``_RESPONSE_SKIPPED_ITEM_TYPES`` are dropped.
      - Unknown / forward-incompatible types are silently dropped (the
        converter fails open so SDK calls don't break on new payload shapes).

    AIDEV-NOTE: fail-open security tradeoff. Per-item exceptions are
    swallowed (``logger.debug`` only) and items with unrecognised shape are
    dropped. If the entire ``input`` list is unconvertible — or yields only
    a ``system`` message from ``instructions`` — the before-hook returns
    without calling :meth:`AIGuardClient.evaluate`, so a sufficiently novel
    or malformed payload can bypass AI Guard rather than block on the
    converter error. This matches Chat's pre-existing behaviour and the AI
    Guard contract that backend transport failures must not break SDK
    calls; the tradeoff is intentional but worth pinning when reasoning
    about coverage.
    """
    result: list[Message] = []

    if instructions:
        result.append(Message(role="system", content=str(instructions)))

    prompt_text = _flatten_prompt_variables(prompt)
    if prompt_text:
        result.append(Message(role="user", content=prompt_text))

    if input_ is None:
        return result

    if isinstance(input_, str):
        # Empty string is treated as no input: evaluating an empty user message
        # surfaces no actionable signal to AI Guard and would bill an extra
        # evaluation per no-op call.
        if input_:
            result.append(Message(role="user", content=input_))
        return result

    if not isinstance(input_, (list, tuple)):
        return result

    for item in input_:
        try:
            if item is None:
                continue
            item_type = _get(item, "type")
            if item_type in _RESPONSE_SKIPPED_ITEM_TYPES:
                continue
            role = _get(item, "role")

            if item_type in ("function_call", "custom_tool_call"):
                ai_msg = Message(role="assistant")
                ai_msg["tool_calls"] = [_function_tool_call_from_item(item)]
                result.append(ai_msg)
                continue

            if item_type == "function_call_output":
                call_id = _get(item, "call_id") or _get(item, "id", "")
                ai_msg = Message(role="tool")
                ai_msg["tool_call_id"] = str(call_id)
                content = _flatten_tool_output(_get(item, "output"))
                if content:
                    ai_msg["content"] = content
                result.append(ai_msg)
                continue

            if item_type == "mcp_call":
                result.extend(_mcp_call_messages(item))
                continue

            if not role:
                continue

            ai_msg = Message(role=role)
            content = _extract_text_content(_get(item, "content"))
            if content is not None:
                ai_msg["content"] = content
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI response input item", exc_info=True)
    return result


def _convert_openai_response_output(resp: Any) -> list[Message]:
    """Convert an OpenAI Response object's ``output`` list to AI Guard messages.

    Item types handled:
      - ``message`` → role+content
      - ``function_call`` / ``custom_tool_call`` → assistant ``tool_calls``
      - ``mcp_call`` → assistant ``tool_calls`` + ``role="tool"`` follow-up
        (see ``_mcp_call_messages``)
    Items in ``_RESPONSE_SKIPPED_ITEM_TYPES`` (``reasoning``, ``mcp_list_tools``)
    are dropped — they are not part of the user-visible conversation.
    """
    result: list[Message] = []
    output = _get(resp, "output") or []
    for item in output:
        try:
            item_type = _get(item, "type", "")
            if item_type in _RESPONSE_SKIPPED_ITEM_TYPES:
                continue
            if item_type == "message":
                role = _get(item, "role", "assistant")
                ai_msg = Message(role=role)
                content = _extract_text_content(_get(item, "content"))
                if content is not None:
                    ai_msg["content"] = content
                result.append(ai_msg)
            elif item_type in ("function_call", "custom_tool_call"):
                ai_msg = Message(role="assistant")
                ai_msg["tool_calls"] = [_function_tool_call_from_item(item)]
                result.append(ai_msg)
            elif item_type == "mcp_call":
                result.extend(_mcp_call_messages(item))
        except Exception:
            logger.debug("Failed to convert OpenAI response output item", exc_info=True)
    return result


def _openai_response_create_before(client: AIGuardClient, kwargs: dict[str, Any]) -> None:
    """Listener for ``openai.responses.create.before``.

    Streaming Responses requests are skipped here — streaming coverage is the
    remit of the streaming-Responses follow-up, which mirrors the
    chat-completions streaming integration's gating-removal pattern.
    """
    if kwargs.get("stream"):
        logger.debug("AI Guard openai responses before-hook skipped: streaming not yet supported")
        return None

    if is_aiguard_context_active():
        logger.debug("AI Guard openai responses before-hook skipped: framework context active")
        return None

    messages = _convert_openai_response_input(
        kwargs.get("instructions"),
        kwargs.get("input"),
        prompt=kwargs.get("prompt"),
    )
    if not messages:
        logger.debug("AI Guard openai responses before-hook skipped: no convertible input messages")
        return None

    if messages[-1].get("role") not in ("user", "tool"):
        logger.debug(
            "AI Guard openai responses before-hook skipped: last message role is %r, expected 'user' or 'tool'",
            messages[-1].get("role"),
        )
        return None

    logger.debug("AI Guard openai responses before-hook evaluating %d message(s)", len(messages))
    try:
        client.evaluate(messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI responses request", exc_info=True)
    return None


def _openai_response_create_after(client: AIGuardClient, kwargs: dict[str, Any], resp: Any) -> None:
    """Listener for ``openai.responses.create.after``.

    Evaluates the full conversation (input + output) after the Responses API
    returns. ``.after`` only fires on non-streaming responses (the contrib
    patch gates the dispatch on ``not stream``); when a framework evaluation
    is already active we skip to avoid double-evaluation.
    """
    if is_aiguard_context_active():
        logger.debug("AI Guard openai responses after-hook skipped: framework context active")
        return None

    request_messages = _convert_openai_response_input(
        kwargs.get("instructions"),
        kwargs.get("input"),
        prompt=kwargs.get("prompt"),
    )
    response_messages = _convert_openai_response_output(resp)

    if not response_messages:
        logger.debug("AI Guard openai responses after-hook skipped: no convertible response messages")
        return None

    all_messages = request_messages + response_messages

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI responses response", exc_info=True)
    return None
