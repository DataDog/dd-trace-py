#!/usr/bin/env python3
"""
Demo script for testing LLMObs.get_prompt() with local RAPID service.

Prerequisites:
    1. Start RAPID service in dd-source:
       cd ~/DataDog/dd-source
       rapid run --service llmobs-prompt-registry

    2. Install dd-trace-py in editable mode:
       cd ~/DataDog/dd-trace-py
       pip install -e .

    3. Set environment variables:
       export DD_API_KEY=test-api-key
       export DD_LLMOBS_ML_APP=demo-app
       export DD_LLMOBS_PROMPTS_ENDPOINT=http://localhost:8080

Usage:
    python demo_get_prompt.py
"""

import os

# Set required environment variables for local testing
os.environ.setdefault("DD_API_KEY", "test-api-key")
os.environ.setdefault("DD_LLMOBS_ML_APP", "demo-app")
os.environ.setdefault("DD_LLMOBS_PROMPTS_ENDPOINT", "http://localhost:8080")

from ddtrace.llmobs import LLMObs


def demo_text_template():
    """Demo: Fetch and render a text template prompt."""
    print("=" * 60)
    print("Demo 1: Text Template Prompt (greeting)")
    print("=" * 60)

    prompt = LLMObs.get_prompt("greeting", label="prod")

    print(f"Prompt ID: {prompt.id}")
    print(f"Version: {prompt.version}")
    print(f"Label: {prompt.label}")
    print(f"Source: {prompt.source}")
    print(f"Template: {prompt.template}")
    print(f"Variables: {prompt.variables}")
    print()

    # Render with variables
    rendered = prompt.format(name="Alice", company="Acme Corp")
    print(f"Rendered: {rendered}")
    print()

    return prompt


def demo_chat_template():
    """Demo: Fetch and render a chat template prompt."""
    print("=" * 60)
    print("Demo 2: Chat Template Prompt (assistant)")
    print("=" * 60)

    prompt = LLMObs.get_prompt("assistant", label="staging")

    print(f"Prompt ID: {prompt.id}")
    print(f"Version: {prompt.version}")
    print(f"Label: {prompt.label}")
    print(f"Source: {prompt.source}")
    print(f"Template (chat): {prompt.template}")
    print(f"Variables: {prompt.variables}")
    print()

    # Render with variables
    rendered = prompt.format(
        company="TechCorp",
        assistant_name="Claude",
        user_message="How do I reset my password?",
    )
    print("Rendered messages:")
    for msg in rendered:
        print(f"  [{msg['role']}]: {msg['content']}")
    print()

    return prompt


def demo_not_found():
    """Demo: Handle prompt not found gracefully."""
    print("=" * 60)
    print("Demo 3: Prompt Not Found (with fallback)")
    print("=" * 60)

    # With user-provided fallback
    prompt = LLMObs.get_prompt(
        "unknown-prompt",
        label="prod",
        fallback="Default greeting: Hello {{name}}!",
    )

    print(f"Prompt ID: {prompt.id}")
    print(f"Version: {prompt.version}")
    print(f"Source: {prompt.source} (should be 'fallback')")
    print(f"Template: {prompt.template}")
    print()

    rendered = prompt.format(name="Bob")
    print(f"Rendered: {rendered}")
    print()

    return prompt


def demo_caching():
    """Demo: Show caching behavior."""
    print("=" * 60)
    print("Demo 4: Caching Behavior")
    print("=" * 60)

    # Clear cache to start fresh
    LLMObs.clear_prompt_cache(l1=True, l2=True)

    # First call - should hit registry
    print("First call (cold cache):")
    prompt1 = LLMObs.get_prompt("greeting", label="prod")
    print(f"  Source: {prompt1.source}")

    # Second call - should hit L1 cache
    print("Second call (hot cache):")
    prompt2 = LLMObs.get_prompt("greeting", label="prod")
    print(f"  Source: {prompt2.source}")

    # Note: source changes from "registry" to "cache" after first fetch
    print()

    return prompt2


def demo_refresh():
    """Demo: Force refresh a prompt."""
    print("=" * 60)
    print("Demo 5: Force Refresh")
    print("=" * 60)

    # Get cached version
    print("Current cached version:")
    prompt1 = LLMObs.get_prompt("greeting", label="prod")
    print(f"  Version: {prompt1.version}, Source: {prompt1.source}")

    # Force refresh from registry
    print("After refresh_prompt():")
    prompt2 = LLMObs.refresh_prompt("greeting", label="prod")
    if prompt2:
        print(f"  Version: {prompt2.version}, Source: {prompt2.source}")
    else:
        print("  Refresh failed (API unreachable)")
    print()

    return prompt2


def main():
    """Run all demos."""
    print("\n" + "=" * 60)
    print("LLM Observability - Prompt Registry Demo")
    print("Using LLMObs.get_prompt() public API")
    print("=" * 60)
    print()

    try:
        demo_text_template()
        demo_chat_template()
        demo_not_found()
        demo_caching()
        demo_refresh()

        print("=" * 60)
        print("All demos completed successfully!")
        print("=" * 60)
    except Exception as e:
        print(f"ERROR: {e}")
        print()
        print("Make sure the RAPID service is running:")
        print("  cd ~/DataDog/dd-source")
        print("  rapid run --service llmobs-prompt-registry")
        raise


if __name__ == "__main__":
    main()
