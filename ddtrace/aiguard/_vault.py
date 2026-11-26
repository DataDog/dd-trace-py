from contextvars import ContextVar
import time
from typing import Any
from typing import Optional

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)

_ACTIVE_VAULT: ContextVar[Optional[dict[str, str]]] = ContextVar("dd_aiguard_active_vault", default=None)
_LAST_VAULT_METRICS: ContextVar[Optional[dict[str, Any]]] = ContextVar("dd_aiguard_last_vault_metrics", default=None)
_LISTENERS_REGISTERED = False


def get_last_vault_metrics() -> Optional[dict[str, Any]]:
    return _LAST_VAULT_METRICS.get()


def _get_active_client():
    from ddtrace.internal.aiguard.product import get_x509_client

    client = get_x509_client()
    if client is None or not client.sds_active():
        return None
    return client


def _collect_strings(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            _collect_strings(item, out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("content", "text", "arguments") or (k == "input" and isinstance(v, (dict, list, str))):
                _collect_strings(v, out)


def _rewrite_strings(obj: Any, it: Any) -> Any:
    if isinstance(obj, str):
        return next(it)
    if isinstance(obj, list):
        return [_rewrite_strings(item, it) for item in obj]
    if isinstance(obj, dict):
        copied = dict(obj)
        for k, v in obj.items():
            if k in ("content", "text", "arguments") or (k == "input" and isinstance(v, (dict, list, str))):
                copied[k] = _rewrite_strings(v, it)
        return copied
    return obj


def _vault_before(kwargs: dict[str, Any], *, include_system: bool) -> None:
    _ACTIVE_VAULT.set(None)
    _LAST_VAULT_METRICS.set(None)

    client = _get_active_client()
    if client is None:
        return

    messages = kwargs.get("messages")
    system = kwargs.get("system") if include_system else None
    if messages is None and system is None:
        return

    texts: list[str] = []
    if system is not None:
        _collect_strings(system, texts)
    if messages is not None:
        if not isinstance(messages, list):
            messages = list(messages)
        _collect_strings(messages, texts)

    if not texts:
        return

    t0 = time.perf_counter()
    tokenized, vault, matched_rule_ids = client.tokenize(texts)
    tokenize_us = round((time.perf_counter() - t0) * 1_000_000.0, 1)

    if not matched_rule_ids:
        _LAST_VAULT_METRICS.set(
            {
                "matched_rule_ids": [],
                "tokens_count": 0,
                "blocked_tool_tokens": {},
                "tokenize_us": tokenize_us,
                "detokenize_us": 0.0,
            }
        )
        return

    it = iter(tokenized)
    if system is not None:
        kwargs["system"] = _rewrite_strings(system, it)
    if messages is not None:
        kwargs["messages"] = _rewrite_strings(messages, it)

    if vault:
        _ACTIVE_VAULT.set(vault)

    _LAST_VAULT_METRICS.set(
        {
            "matched_rule_ids": matched_rule_ids,
            "tokens_count": len(vault),
            "blocked_tool_tokens": {},
            "tokenize_us": tokenize_us,
            "detokenize_us": 0.0,
        }
    )


def _vault_openai_before(kwargs: dict[str, Any]) -> None:
    try:
        _vault_before(kwargs, include_system=False)
    except Exception:
        logger.debug("AI Guard SDS vault openai before-hook failed", exc_info=True)


def _vault_anthropic_before(kwargs: dict[str, Any]) -> None:
    try:
        _vault_before(kwargs, include_system=True)
    except Exception:
        logger.debug("AI Guard SDS vault anthropic before-hook failed", exc_info=True)


def _detokenize_value(client: Any, val: Any, vault: dict[str, str], tool_name: Optional[str], blocked: list[str]) -> Any:
    if isinstance(val, str):
        new_s, blk = client.detokenize(val, vault, tool_name)
        blocked.extend(blk)
        return new_s
    if isinstance(val, list):
        return [_detokenize_value(client, item, vault, tool_name, blocked) for item in val]
    if isinstance(val, dict):
        return {k: _detokenize_value(client, v, vault, tool_name, blocked) for k, v in val.items()}
    return val


def finish_vault_call(resp: Any = None, *, provider: str = "openai") -> None:
    vault = _ACTIVE_VAULT.get()
    _ACTIVE_VAULT.set(None)
    if not vault or resp is None:
        return

    from ddtrace.internal.aiguard.product import get_x509_client

    client = get_x509_client()
    if client is None:
        return

    t0 = time.perf_counter()
    blocked_by_tool: dict[str, list[str]] = {}
    try:
        if provider == "openai":
            choices = getattr(resp, "choices", None) or []
            for choice in choices:
                msg = getattr(choice, "message", None)
                if msg is None:
                    continue
                content = getattr(msg, "content", None)
                if isinstance(content, str) and content:
                    new_content, _ = client.detokenize(content, vault, None)
                    msg.content = new_content
                tool_calls = getattr(msg, "tool_calls", None) or []
                for tc in tool_calls:
                    fn = getattr(tc, "function", None)
                    if fn is None:
                        continue
                    tool_name = getattr(fn, "name", None)
                    args = getattr(fn, "arguments", None)
                    if isinstance(args, str) and args:
                        new_args, blk = client.detokenize(args, vault, tool_name)
                        fn.arguments = new_args
                        if blk and tool_name:
                            blocked_by_tool[tool_name] = sorted(set(blocked_by_tool.get(tool_name, []) + blk))
        elif provider == "anthropic":
            blocks = getattr(resp, "content", None) or []
            for block in blocks:
                btype = getattr(block, "type", None)
                if btype == "text":
                    text = getattr(block, "text", None)
                    if isinstance(text, str) and text:
                        new_text, _ = client.detokenize(text, vault, None)
                        block.text = new_text
                elif btype == "tool_use":
                    tool_name = getattr(block, "name", None)
                    inp = getattr(block, "input", None)
                    if inp is not None:
                        blk: list[str] = []
                        block.input = _detokenize_value(client, inp, vault, tool_name, blk)
                        if blk and tool_name:
                            blocked_by_tool[tool_name] = sorted(set(blocked_by_tool.get(tool_name, []) + blk))
    except Exception:
        logger.debug("AI Guard SDS vault re-hydration failed", exc_info=True)
    finally:
        detokenize_us = round((time.perf_counter() - t0) * 1_000_000.0, 1)
        metrics = _LAST_VAULT_METRICS.get()
        if metrics is not None:
            metrics["detokenize_us"] = detokenize_us
            metrics["blocked_tool_tokens"] = blocked_by_tool


def register_vault_listeners() -> None:
    global _LISTENERS_REGISTERED
    if _LISTENERS_REGISTERED:
        return
    core.on("openai.chat.completions.create.before", _vault_openai_before)
    core.on("anthropic.messages.create.before", _vault_anthropic_before)
    _LISTENERS_REGISTERED = True
