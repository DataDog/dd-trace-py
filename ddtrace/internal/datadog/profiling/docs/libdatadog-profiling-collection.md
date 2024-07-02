# Using libdatadog for profiling export

How to use the `libdatadog` library to collect and export profiling data.

## Context

A profiler periodically collects information about a running program.
This information consists of call stacks, values (such as cpu time), and metadata (including the current span, task ID, and other circumstantial information).
Once a profiler has gathered this information, it must collect it into into intermediate storage and then render it to a serialized format.
This document is concerned with the interface for that storage, not the act of taking samples.

`dd-trace-py` includes profiling samplers and a collection system for the data they produce.
Some of the components for that system are implemented in cython or C.
However, they all interact with the Python runtime, create Python objects, and present a Python API.
In that sense, the profiling collection system is implemented in Python.
A design like this is intuitive and desirable in a Python project, but as its feature set has grown, some limitations have emerged:

- Collecting data in pure-Python is becoming a source of performance overhead.
- Serializing that information is expensive, and doing so requires taking a significant amount of GIL time.
- There are other profilers for other runtimes all of which adhere to a common data model, and so would be able to benefit from a common utility library for collecting and exporting profiling data.
- Some profiling features are better implemented in pure-native code, outside of the control of the runtime--hooking them into runtime-laden interfaces can be cumbersome or even dangerous.

Early experiments showed great promise for switching to [libdatadog](https://github.com/datadog/libdatadog) for collection and export.
However, a production-quality implementation covers several considerations and introduces a number of new code paths.


## Requirements

### Simultaneity
Within a single program, many samplers may be active simultaneously.
Moreover, there is no guarantee that, in the process of collecting a sample, a sampler will not be interrupted by another sampler.
Thus, a complete solution must provide this flexibility.


### Thread-Fork Safety
The implementation of `ddtrace` utilizes many Python threads, and may also utilize native threads which are not controlled by the Python runtime.
Meanwhile, the underlying user application may call `fork()` at any time.
A complete solution must be able to safely resume operation after a `fork()`.
More detail is provided in section XXX.


### Native and Python Interfaces
As described in the accompanying document, `stack-sampler-v2.md`, an ideal collection system would support not only a Python API, but also a native one.

## Design


### Samples

A Sample is a collection of data representing one observation.
All types of observations (e.g., allocations, locks, CPU) are represented by the same data structure, since the libdatadog API assumes any sample may contain one or all of the possible sample types.
When `start_sample()` is called, an opaque pointer is returned, which must then be passed to all subsequent operations on the sample.

In a given moment, in a world where samplers may be on native threads, there may be multiple samples in flight.
A sampler may also be interrupted by another sampler on the same thread.
Samplers should not be interrupted by the same type of sampler in other threads.
However, this last case hardly matters, since the interrupted context will resume using the Sample it was given.

Samples are interacted with transactionally, for example

1. `ddup_start_sample()` (every sample begins with a call to `start_sample()`)
2. adding frames, walltime, tags, etc
3. ddup_flush_sample() (every sample ends with a call to `flush_sample()`)

The act of flushing a sample stores its data in a ddog_prof_Profile object (which is wrapped by Profile in this code).
it also releases the Sample (don't reuse Samples in application code!)

There's one wrinkle here.
The navigation through frame data (unwinding) may result in temporary strings.
We need to cache these strings.
In order to minimize overhead, strings are cached (and de-duplicated) in a cache attached to the Profile rather than the Sample.


### Profile

A Profile wraps the collection of samples.
A Profile is periodically flushed to the Datadog backend during an upload operation.
A profile actually manages its own internal cache of strings, which makes it slightly unfortunate that we de-duplicate strings _twice_.
This is a little bit of a wart, but in practice we're still way under the memory overhead of the pure-Python collection system in mainline dd-trace-py.

For simplicity, the Profile object maintains two `ddog_prof_Profile`s using a red-black swap mechanism.
When one Profile is consumed by the uploader, it gets swapped with the other profile.
This probably isn't actually necessary anymore.


### Uploader

A special class for managing the upload operations.
It holds onto some state which is attached to a Profiling upload.
This is actually a little bit delicate because the underlying libdatadog fixture calls an HTTP library which may be more prone to tearing during `fork()` than other parts of the code.
Accordingly, we register `atfork()` handlers in `interface.cpp` to try and protect it.


### Builders

Conceptually, the Uploader can be reused.
However, there is some anxiety around exactly what degree of safety we can guarantee when an upload (not obvious: uploads happen in a thread controlled by a libdatadog dependency) is cut by a `fork()`, so we rebuild the uploader fresh every time.
