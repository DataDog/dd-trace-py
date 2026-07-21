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


def gc_samples(profile: Any, pprof_utils: Any) -> list[Any]:
    wall_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    return [
        sample
        for sample in wall_samples
        if any(
            pprof_utils.get_location_from_id(profile, location_id).function_name == "Garbage collection"
            for location_id in sample.location_id
        )
    ]
