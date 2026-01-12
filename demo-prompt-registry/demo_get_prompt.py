#!/usr/bin/env python3
"""
Demo script for testing llmobs get_prompt() with local RAPID service.

Prerequisites:
    1. Start RAPID service in dd-source:
       cd ~/DataDog/dd-source
       rapid run --service llmobs-prompt-registry

    2. Install dd-trace-py in editable mode:
       cd ~/DataDog/dd-trace-py
       pip install -e .

    3. Set environment variable for local endpoint:
       export DD_LLMOBS_PROMPTS_ENDPOINT=http://localhost:8080

Usage:
    python demo_get_prompt.py
"""

import os

# Set required environment variables for local testing
os.environ["DD_LLMOBS_PROMPTS_ENDPOINT"] = "http://localhost:8080"

from ddtrace.llmobs._prompts.manager import PromptManager


def demo_text_template():
    """Demo: Fetch and render a text template prompt."""
    print("=" * 60)
    print("Demo 1: Text Template Prompt (greeting)")
    print("=" * 60)

    manager = PromptManager(
        api_key="test-api-key",
        site="localhost:8080",  # Not used when DD_LLMOBS_PROMPTS_ENDPOINT is set
        ml_app="demo-app",
    )

    prompt = manager.get_prompt("greeting", label="prod")

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

    manager = PromptManager(
        api_key="test-api-key",
        site="localhost:8080",
        ml_app="demo-app",
    )

    prompt = manager.get_prompt("assistant", label="staging")

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

    manager = PromptManager(
        api_key="test-api-key",
        site="localhost:8080",
        ml_app="demo-app",
    )

    # With user-provided fallback
    prompt = manager.get_prompt(
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

    manager = PromptManager(
        api_key="test-api-key",
        site="localhost:8080",
        ml_app="demo-app",
    )

    # First call - should hit registry
    print("First call (cold cache):")
    prompt1 = manager.get_prompt("greeting", label="prod")
    print(f"  Source: {prompt1.source}")

    # Second call - should hit L1 cache
    print("Second call (hot cache):")
    prompt2 = manager.get_prompt("greeting", label="prod")
    print(f"  Source: {prompt2.source}")

    # Note: source changes from "registry" to "cache" after first fetch
    print()

    return prompt2


def main():
    """Run all demos."""
    print("\n" + "=" * 60)
    print("LLM Observability - Prompt Registry Demo")
    print("=" * 60)
    print()

    try:
        demo_text_template()
        demo_chat_template()
        demo_not_found()
        demo_caching()

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
