"""Example demonstrating how to push custom events to the profiler.

This shows how to use the new push_event() API to send custom events
(like asyncio task starts) to your profiles.
"""
import asyncio
from typing import Optional

from ddtrace.internal.datadog.profiling import ddup


def example_simple_event() -> None:
    """Push a simple event with no stack trace."""
    ddup.push_event("my_custom_event", capture_stack=False)


def example_event_with_stack() -> None:
    """Push an event with a stack trace (default behavior)."""
    ddup.push_event("function_called")


def example_event_with_labels() -> None:
    """Push an event with custom labels."""
    labels = {
        "task_name": "my_task",
        "parent_task": "parent_id_123",
        "status": "started",
    }
    ddup.push_event("task_start", labels=labels)


def example_event_with_limited_stack() -> None:
    """Push an event with a limited stack trace."""
    ddup.push_event("checkpoint", max_nframes=10)


async def track_asyncio_task(task_name: str, parent_task: Optional[str] = None) -> None:
    """Example: Track an asyncio task lifecycle."""
    labels = {"task_name": task_name}
    if parent_task:
        labels["parent_task"] = parent_task
    
    # Push task start event
    ddup.push_event("asyncio_task_start", labels=labels)
    
    try:
        # Simulate some work
        await asyncio.sleep(0.1)
        
        # Push checkpoint events as needed
        ddup.push_event("asyncio_task_checkpoint", labels={**labels, "checkpoint": "1"})
        
        await asyncio.sleep(0.1)
    
    finally:
        # Push task end event
        ddup.push_event("asyncio_task_end", labels=labels)


def example_manual_sample_creation() -> None:
    """Example: Manually create a sample with more control."""
    handle = ddup.SampleHandle()
    
    # Push the event type (this makes it an Event sample)
    handle.push_event("custom_event")
    
    # Add custom labels
    handle.push_label("label1", "value1")
    handle.push_label("label2", "value2")
    
    # Optionally add stack frames manually
    import sys
    frame = sys._getframe(0)
    handle.push_frame("my_function", "my_file.py", 0, frame.f_lineno)
    
    # Flush the sample to the profile
    handle.flush_sample()


if __name__ == "__main__":
    # Initialize the profiler
    ddup.init(
        service="my-service",
        env="dev",
        version="1.0.0",
    )
    ddup.start()
    
    # Run examples
    example_simple_event()
    example_event_with_stack()
    example_event_with_labels()
    example_event_with_limited_stack()
    example_manual_sample_creation()
    
    # Run async example
    asyncio.run(track_asyncio_task("main_task"))
    
    # Upload the profile
    ddup.upload()

