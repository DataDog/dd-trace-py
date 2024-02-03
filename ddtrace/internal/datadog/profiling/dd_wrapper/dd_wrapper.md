dd_wrapper
===

This is experimental code that uses libdatadog for the collection and propagation of profiling data. In particular, this library has its own test suite which runs apart from the standard dd-trace-py tests. Why?  Because the experimental features haven't been fully incorporated into the mainline codebase yet, and I didn't want to waste time figuring out how to get all the build-time components in the right place within the standard test fixture (we may end up just using a container).
