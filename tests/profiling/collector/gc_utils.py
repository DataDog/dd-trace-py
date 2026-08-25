from typing import Any


def ddtrace_gc_callbacks(gc: Any) -> list[Any]:
    return [callback for callback in gc.callbacks if getattr(callback, "__name__", None) == "_ddtrace_gc_callback"]


def slow_cyclic_collection(delay: float = 0.75) -> None:
    import gc
    import time

    class SlowCycle:
        def __init__(self) -> None:
            self.cycle = self

        def __del__(self) -> None:
            time.sleep(delay)

    cycle = SlowCycle()
    del cycle
    gc.collect()


async def slow_cyclic_collection_coroutine(delay: float = 0.75) -> None:
    """Collect a slow cycle from the coroutine's own frame, so the GC marker lands on a Task frame."""
    import gc
    import time

    class SlowCycle:
        def __init__(self) -> None:
            self.cycle = self

        def __del__(self) -> None:
            time.sleep(delay)

    cycle = SlowCycle()
    del cycle
    gc.collect()


def busy_cyclic_collection(duration: float = 1.0) -> None:
    """Burn CPU inside a collection, so the collecting thread reports CPU time."""
    import gc
    import time

    class BusyCycle:
        def __init__(self) -> None:
            self.cycle = self

        def __del__(self) -> None:
            end = time.monotonic() + duration
            while time.monotonic() < end:
                pass

    cycle = BusyCycle()
    del cycle
    gc.collect()


def gc_samples(profile: Any, pprof_utils: Any, value_type: str = "wall-time") -> list[Any]:
    samples = pprof_utils.get_samples_with_value_type(profile, value_type)
    return [
        sample
        for sample in samples
        if any(
            pprof_utils.get_location_from_id(profile, location_id).function_name == "Garbage collection"
            for location_id in sample.location_id
        )
    ]
