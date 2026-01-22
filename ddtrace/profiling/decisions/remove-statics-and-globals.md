# Removing statics and globals from the Profiling codebase

Today (January 2026) the Python Profiling C++ codebase contains a lot of global and static variables.

Those fall into a few categories:
- Global symbols that reflect Python state (Thread Info Map, TODO)
- Global symbols used for configuration (TODO)
- Global symbols used to store some kind of state (String Table, Frame Cache)
- Static symbols used for caching (TODO)

Working with statics and globals come with advantages, but also obvious downsides so we would like to clean them up.

This document aims at describing how we do that.
- **Example 1 [#15801](https://github.com/DataDog/dd-trace-py/pull/15801)**
    - We take `inline FrameStack python_stack` and make it a field in `ThreadInfo`
    - This matches the scope in which we typically use this variable -- it's reset between calls to `unwind_tasks`.
- **Example 2 [#15806](https://github.com/DataDog/dd-trace-py/pull/15806)**
    - We remove the global `std::vector<std::unique_ptr<StackInfo>>`'s `current_greenlets` and `current_tasks`
    and have them as attributes of `ThreadInfo` (similar to **Example 1**)
- **Example 3 [#15805](https://github.com/DataDog/dd-trace-py/pull/15805)**
    - We remove some variables that are completely unused which we inherited from echion (e.g. `sampler_thread`)
    - We eliminate the global `inline _PyRuntimeState* runtime = &_PyRuntime` and replace it with a local variable
    in `Sampler::sampling_thread`. It thus has the same lifetime as the `Sampler`, which matches what we want.
- **Example 4 [#15830](https://github.com/DataDog/dd-trace-py/pull/15830)**
    - We remove the globals `std::unordered_map<uintptr_t, ThreadInfo::Ptr> thread_info_map_` and 
    `std::mutex thread_info_map_lock_`
    - Since they maintain state for Echion (internal concepts) but need to match the lifetime of the `Sampler`, we introduce a new `EchionSampler` class...
    - ... this class has the two variables as attributes, and we instantiate it in `Sampler::sampling_thread`
    - ... we then pass the class instance to `for_each_thread` and friends so they can use the state