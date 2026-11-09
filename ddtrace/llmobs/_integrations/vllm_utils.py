"""Prompt parsing for the vLLM LLMObs integration."""

import re
from typing import Optional

from ddtrace.llmobs.types import Message


# Role patterns: (quick_check_marker, regex_pattern)
# Quick check avoids running all regexes - we first check if marker exists in prompt
_ROLE_PATTERNS = [
    # Llama 3: <|start_header_id|>role<|end_header_id|>
    ("<|start_header_id|>", re.compile(r"<\|start_header_id\|>(system|user|assistant)<\|end_header_id\|>")),
    # Llama 4: <|header_start|>role<|header_end|>
    ("<|header_start|>", re.compile(r"<\|header_start\|>(system|user|assistant)<\|header_end\|>")),
    # Granite: <|start_of_role|>role<|end_of_role|> (includes document/documents roles)
    ("<|start_of_role|>", re.compile(r"<\|start_of_role\|>(system|user|assistant|documents?)<\|end_of_role\|>")),
    # Gemma: <start_of_turn>role (uses "model" for assistant)
    ("<start_of_turn>", re.compile(r"<start_of_turn>(system|user|model)")),
    # MiniMax: <beginning_of_sentence>role
    ("<beginning_of_sentence>", re.compile(r"<beginning_of_sentence>(system|user|ai)")),
    # ChatML/Qwen/Hermes: <|im_start|>role
    ("<|im_start|>", re.compile(r"<\|im_start\|>(system|user|assistant)")),
    # DeepSeek VL2: <|User|>: / <|Assistant|>: (normal | with colon)
    ("<|User|>:", re.compile(r"<\|(User|Assistant)\|>:")),
    # Phi: <|system|> / <|user|> / <|assistant|>
    ("<|system|>", re.compile(r"<\|(system|user|assistant)\|>")),
    ("<|user|>", re.compile(r"<\|(system|user|assistant)\|>")),
    # DeepSeek V3 (fullwidth ｜ - U+FF5C, not the same as |): <｜User｜>
    ("<｜", re.compile(r"<｜(User|Assistant)｜>")),
    # TeleFLM: <_role>
    ("<_user>", re.compile(r"<_(system|user|bot)>")),
    ("<_system>", re.compile(r"<_(system|user|bot)>")),
    # Inkbot: <#role#>
    ("<#user#>", re.compile(r"<#(system|user|bot)#>")),
    ("<#system#>", re.compile(r"<#(system|user|bot)#>")),
    # Alpaca: ### Instruction: / ### Response:
    ("### Instruction", re.compile(r"### (Instruction|Response|Input):")),
    # Falcon: Role: prefix (capitalized)
    ("User:", re.compile(r"^(System|User|Assistant|Falcon): ?", re.MULTILINE)),
]


# End-of-turn markers to strip (case-sensitive)
_END_MARKERS = re.compile(
    r"<\|im_end\|>|<\|eot_id\|>|<\|end\|>|<\|eot\|>|<\|eom\|>|"
    r"<\|end_of_text\|>|<end_of_turn>|<end_of_sentence>|<\|eos\|>|"
    r"<｜end▁of▁sentence｜>",
)

# Roles that represent the "assistant" in various templates
_ASSISTANT_ROLES = ("assistant", "model", "ai", "bot", "response", "falcon")


def parse_prompt_to_messages(prompt: Optional[str]) -> list[Message]:
    """Parse a formatted prompt into structured messages."""
    if not prompt:
        return []

    # Quick check: find first matching marker, then use its pattern
    for marker, pattern in _ROLE_PATTERNS:
        if marker in prompt:
            messages = _parse_with_pattern(prompt, pattern)
            if messages:
                return messages

    # No recognized format - return raw prompt as single message
    return [Message(role="", content=prompt)]


def _parse_with_pattern(prompt: str, role_pattern: re.Pattern[str]) -> list[Message]:
    """Parse prompt using a specific role pattern."""
    matches = list(role_pattern.finditer(prompt))
    if not matches:
        return []

    messages: list[Message] = []
    for i, match in enumerate(matches):
        role_match = match.group(1)
        if not role_match:
            continue
        role = role_match.lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(prompt)
        content = _END_MARKERS.sub("", prompt[start:end]).lstrip(":").strip()

        # Skip empty trailing assistant-like roles (generation prompt marker)
        if role in _ASSISTANT_ROLES and not content and i == len(matches) - 1:
            continue

        messages.append(Message(role=role, content=content))

    return messages
