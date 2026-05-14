"""AI Guard integration for the OpenAI SDK.

Provides listener functions for ``openai.chat.completions.create.before``
and ``openai.chat.completions.create.after`` events dispatched from the
OpenAI contrib patching layer
"""

from collections import deque
import threading

from ddtrace.appsec._ai_guard._context import is_aiguard_context_active
from ddtrace.appsec.ai_guard._api_client import AIGuardAbortError
from ddtrace.appsec.ai_guard._api_client import Function
from ddtrace.appsec.ai_guard._api_client import Message
from ddtrace.appsec.ai_guard._api_client import Options
from ddtrace.appsec.ai_guard._api_client import ToolCall
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)


# AIDEV-NOTE: The compound ``OpenAIAIGuardAbortError`` class is built lazily on
# first block. ``_openai.py`` is imported unconditionally by the AI Guard
# listener (see ``_listener.py``), but the OpenAI SDK is an optional runtime
# dependency — eagerly importing ``openai`` here would break AI Guard
# initialization in environments that only use a different provider.
#
_openai_abort_error_cls = None

# The lazy build uses double-checked locking: the unsynchronised check-then-act
# pattern would let two threads simultaneously enter the build branch and each
# create their own class, so callers that captured ``type(err)`` from one block
# would silently stop matching errors from a concurrent block.
_openai_abort_error_cls_lock = threading.Lock()


def _get_openai_abort_error_cls():
    """Return ``OpenAIAIGuardAbortError`` (cached), or ``None`` if openai is not importable.

    The class inherits from ``openai.UnprocessableEntityError`` (status 422 —
    the "policy rejection" semantics used by AI Gateway) and from
    ``AIGuardAbortError`` so existing Datadog-specific handlers keep working.
    """
    global _openai_abort_error_cls
    # Fast path: already cached, no lock needed (assignment to a module
    # attribute is atomic under the GIL).
    if _openai_abort_error_cls is not None:
        return _openai_abort_error_cls

    with _openai_abort_error_cls_lock:
        # Re-check inside the lock: another thread may have built the class
        # while we were blocked on acquisition.
        if _openai_abort_error_cls is not None:
            return _openai_abort_error_cls

        try:
            import openai
        except ImportError:
            return None

        class OpenAIAIGuardAbortError(openai.UnprocessableEntityError, AIGuardAbortError):
            """AI Guard abort error compatible with the OpenAI SDK error hierarchy.

            Catchable as either ``openai.APIError`` / ``openai.UnprocessableEntityError``
            (idiomatic OpenAI error handling, no retry on 422) or
            ``AIGuardAbortError`` (Datadog-specific, exposes ``action`` / ``reason``).

            AIDEV-NOTE: catchability asymmetry vs plain ``AIGuardAbortError``.
            ``AIGuardAbortError`` derives from ``DDBlockException(BaseException)``
            so a generic ``except Exception:`` does NOT catch it (by design — a
            block decision must not be silently swallowed). However,
            ``OpenAIAIGuardAbortError`` *also* inherits from
            ``openai.UnprocessableEntityError``, which is ``Exception``-derived,
            so via MRO this subclass IS catchable by ``except Exception:``.
            That asymmetry is intentional: the OpenAI contrib must keep
            ``except openai.APIError:`` blocks working unchanged for users
            migrating from non-AI-Guard error handling. Code that wants
            uniform block detection across providers should branch on
            ``isinstance(e, AIGuardAbortError)``.
            """

            def __init__(self, action, reason, tags=None, sds=None, tag_probs=None):
                self.action = action
                self.reason = reason
                self.tags = tags
                self.sds = sds or []
                self.tag_probs = tag_probs

                message = f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')"
                # AIDEV-NOTE: We can't call ``openai.UnprocessableEntityError.__init__``
                # here — its MRO super() chain ends up at ``AIGuardAbortError.__init__``
                # (which requires ``(action, reason)``), not ``Exception.__init__``,
                # so the ``super().__init__(message)`` deep in ``APIError`` raises
                # ``TypeError: missing 'reason'``. Initialize the OpenAI-side
                # attributes directly instead. The block originates from AI Guard,
                # not an OpenAI HTTP call, so ``response`` / ``request`` / ``request_id``
                # are ``None``; only ``status_code`` (422) and ``body`` (the AI Guard
                # decision payload) carry semantic meaning.
                self.message = message
                self.request = None
                self.body = {"action": action, "reason": reason, "source": "datadog_ai_guard"}
                self.code = "ai_guard_block"
                self.param = None
                self.type = "ai_guard_abort"
                self.response = None
                self.status_code = 422
                self.request_id = None
                Exception.__init__(self, message)

        _openai_abort_error_cls = OpenAIAIGuardAbortError
        return _openai_abort_error_cls


def _wrap_abort_error(cause):
    """Wrap an ``AIGuardAbortError`` into the OpenAI-compatible variant.

    Falls back to returning the original ``cause`` when the OpenAI SDK is not
    importable — the listener still surfaces a block, just without OpenAI
    exception-hierarchy compatibility.
    """
    cls = _get_openai_abort_error_cls()
    if cls is None:
        return cause
    wrapped = cls(
        action=cause.action,
        reason=cause.reason,
        tags=cause.tags,
        sds=cause.sds,
        tag_probs=cause.tag_probs,
    )
    wrapped.__cause__ = cause
    return wrapped


def _get(obj, key, default=None):
    """Read *key* from a dict or object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _convert_openai_messages(messages):
    """Convert a list of OpenAI chat messages to AI Guard ``Message`` format.

    Handles both plain dicts (user-supplied) and SDK response objects
    (which expose attributes instead of dict keys).

    Legacy translation: OpenAI's deprecated single-call API still ships in
    ``openai>=1.102.0`` (our minimum supported version). Assistant
    ``function_call`` and ``role="function"`` response messages are
    translated to AI Guard's canonical ``tool_calls`` / ``role="tool"``
    shape — the ``Message`` schema in ``_api_client.py`` only models
    ``tool_calls``/``tool_call_id``, so without translation the function
    name and arguments would be invisible to the evaluator. Pairing uses a
    FIFO of synthetic ids ``fc_N`` so the matching ``role="function"``
    result correlates with the assistant turn that issued it. Mirrors the
    canonical pattern in ``ai_guard/integrations/litellm.py``.
    """
    result = []
    pending_fc_ids: deque = deque()
    fc_counter = 0
    for msg in messages:
        try:
            if msg is None:
                continue
            role = _get(msg, "role", "")
            if not role:
                continue

            if role == "function":
                ai_msg = Message(role="tool")
                ai_msg["tool_call_id"] = pending_fc_ids.popleft() if pending_fc_ids else ""
            else:
                ai_msg = Message(role=role)
                tool_call_id = _get(msg, "tool_call_id")
                if tool_call_id:
                    ai_msg["tool_call_id"] = tool_call_id

            content = _get(msg, "content")
            if content is not None:
                ai_msg["content"] = content

            if role == "assistant":
                tool_calls_out = []
                tool_calls = _get(msg, "tool_calls")
                if tool_calls:
                    tool_calls_out.extend(_tool_call_from(tc) for tc in tool_calls)
                function_call = _get(msg, "function_call")
                if function_call:
                    synthetic_id = f"fc_{fc_counter}"
                    fc_counter += 1
                    pending_fc_ids.append(synthetic_id)
                    tool_calls_out.append(
                        ToolCall(
                            id=synthetic_id,
                            function=Function(
                                name=_get(function_call, "name", "") or "",
                                arguments=_get(function_call, "arguments", "{}") or "{}",
                            ),
                        )
                    )
                if tool_calls_out:
                    ai_msg["tool_calls"] = tool_calls_out
            else:
                tool_calls = _get(msg, "tool_calls")
                if tool_calls:
                    ai_msg["tool_calls"] = [_tool_call_from(tc) for tc in tool_calls]
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI message", exc_info=True)
    return result


def _tool_call_from(tc):
    """Build a ``ToolCall`` from an OpenAI tool_call (dict or SDK object)."""
    fn = _get(tc, "function") or {}
    return ToolCall(
        id=_get(tc, "id", ""),
        function=Function(
            name=_get(fn, "name", ""),
            arguments=_get(fn, "arguments", "{}"),
        ),
    )


def _convert_openai_response(resp):
    """Convert an OpenAI ChatCompletion response to AI Guard ``Message`` list."""
    result = []
    choices = _get(resp, "choices") or []
    for choice in choices:
        try:
            message = _get(choice, "message")
            if message is None:
                continue
            role = _get(message, "role", "assistant")
            ai_msg = Message(role=role)

            content = _get(message, "content")
            if content is not None:
                ai_msg["content"] = content

            tool_calls_out = []
            tool_calls = _get(message, "tool_calls")
            if tool_calls:
                tool_calls_out.extend(_tool_call_from(tc) for tc in tool_calls)
            function_call = _get(message, "function_call")
            if function_call:
                tool_calls_out.append(
                    ToolCall(
                        id=f"fc_{id(function_call):x}",
                        function=Function(
                            name=_get(function_call, "name", "") or "",
                            arguments=_get(function_call, "arguments", "{}") or "{}",
                        ),
                    )
                )
            if tool_calls_out:
                ai_msg["tool_calls"] = tool_calls_out
            result.append(ai_msg)
        except Exception:
            logger.debug("Failed to convert OpenAI response message", exc_info=True)
    return result


def _openai_chat_completion_before(client, kwargs):
    """Listener for ``openai.chat.completions.create.before``."""
    if is_aiguard_context_active():
        logger.debug("AI Guard openai before-hook skipped: framework context active (e.g. Strands plugin)")
        return None

    messages = kwargs.get("messages")
    if messages is None:
        logger.debug("AI Guard openai before-hook skipped: kwargs has no 'messages' key")
        return None

    if not isinstance(messages, (list, tuple)):
        messages = list(messages)
        kwargs["messages"] = messages
    if not messages:
        logger.debug("AI Guard openai before-hook skipped: messages is empty")
        return None

    ai_guard_messages = _convert_openai_messages(messages)
    if not ai_guard_messages:
        logger.debug("AI Guard openai before-hook skipped: no convertible messages")
        return None

    if ai_guard_messages[-1].get("role") not in ("user", "tool"):
        logger.debug(
            "AI Guard openai before-hook skipped: last message role is %r, expected 'user' or 'tool'",
            ai_guard_messages[-1].get("role"),
        )
        return None

    logger.debug("AI Guard openai before-hook evaluating %d message(s)", len(ai_guard_messages))
    try:
        client.evaluate(ai_guard_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion request", exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Responses API (``openai.responses.create`` / ``openai.responses.parse``)
#
# Different request shape than Chat Completions:
#  - ``instructions`` (str, system-prompt-like) and ``input`` (str | list of
#    items) replace the ``messages=[...]`` list.
#  - Items in ``input`` may be regular role messages OR ``function_call`` /
#    ``function_call_output`` items (top-level, not wrapped in an assistant
#    message like Chat Completions' ``tool_calls``).
#
# Response shape: ``resp.output`` is a list of items (vs ``resp.choices``)
# containing ``message``, ``function_call``, ``reasoning``, ``mcp_call`` etc.
#
# Conversion target is the same AI Guard ``Message`` schema used for Chat
# Completions, so a tenant's AI Guard rules apply uniformly across both APIs.
# ---------------------------------------------------------------------------


# Responses-API content blocks that carry user-visible text. Restricting to
# this allowlist avoids picking up stray ``text`` fields on unrelated block
# shapes (e.g. an annotation block with a "text" attribute that isn't user
# content).
_RESPONSE_TEXT_BLOCK_TYPES = ("input_text", "output_text", "text")
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


def _extract_text_content(content):
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


def _flatten_tool_output(output):
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


def _function_tool_call_from_item(item):
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


def _mcp_call_messages(item):
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


def _convert_openai_response_input(instructions, input_):
    """Convert ``instructions`` + ``input`` to AI Guard ``Message`` list.

    ``instructions`` becomes a leading system message when set. ``input`` may
    be ``None``, a plain string (single user message), or a list of items:
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
    """
    result = []

    if instructions:
        result.append(Message(role="system", content=str(instructions)))

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


def _convert_openai_response_output(resp):
    """Convert an OpenAI Response object's ``output`` list to AI Guard messages.

    Item types handled:
      - ``message`` → role+content
      - ``function_call`` / ``custom_tool_call`` → assistant ``tool_calls``
      - ``mcp_call`` → assistant ``tool_calls`` + ``role="tool"`` follow-up
        (see ``_mcp_call_messages``)
    Items in ``_RESPONSE_SKIPPED_ITEM_TYPES`` (``reasoning``, ``mcp_list_tools``)
    are dropped — they are not part of the user-visible conversation.
    """
    result = []
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


def _openai_response_create_before(client, kwargs):
    """Listener for ``openai.responses.create.before``.

    PR 1 of the Responses API rollout skips streaming requests; streaming
    coverage is delivered in a follow-up PR (mirrors the chat-completions
    streaming PR which dropped this gate).
    """
    if kwargs.get("stream"):
        logger.debug("AI Guard openai responses before-hook skipped: streaming not yet supported")
        return None

    if is_aiguard_context_active():
        logger.debug("AI Guard openai responses before-hook skipped: framework context active")
        return None

    messages = _convert_openai_response_input(kwargs.get("instructions"), kwargs.get("input"))
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


def _openai_response_create_after(client, kwargs, resp):
    """Listener for ``openai.responses.create.after``.

    Evaluates the full conversation (input + output) after the Responses API
    returns. ``.after`` only fires on non-streaming responses (the contrib
    patch gates the dispatch on ``not stream``); when a framework evaluation
    is already active we skip to avoid double-evaluation.
    """
    if is_aiguard_context_active():
        logger.debug("AI Guard openai responses after-hook skipped: framework context active")
        return None

    request_messages = _convert_openai_response_input(kwargs.get("instructions"), kwargs.get("input"))
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


def _openai_chat_completion_after(client, kwargs, resp):
    """Listener for ``openai.chat.completions.create.after``.

    Evaluates the full conversation (request + response) after the LLM
    returns.  Skips streaming responses (handled separately) and when a
    framework evaluation is already active.

    On block: raises an ``OpenAIAIGuardAbortError`` (or plain
    ``AIGuardAbortError`` when the OpenAI SDK is not importable).  Allow /
    skip paths return ``None``.
    """
    if is_aiguard_context_active():
        logger.debug("AI Guard openai after-hook skipped: framework context active (e.g. Strands plugin)")
        return None

    request_messages = _convert_openai_messages(kwargs.get("messages", []))
    response_messages = _convert_openai_response(resp)

    if not response_messages:
        logger.debug("AI Guard openai after-hook skipped: no convertible response messages")
        return None

    all_messages = request_messages + response_messages

    try:
        client.evaluate(all_messages, Options(block=ai_guard_config._ai_guard_block))
    except AIGuardAbortError as e:
        raise _wrap_abort_error(e)
    except Exception:
        logger.debug("Failed to evaluate OpenAI chat completion response", exc_info=True)
    return None
