# When run via ddtrace-run with the environment variables
#
#   DD_PROFILING_HEAP_SAMPLE_SIZE=8
#   DD_PROFILING_ENABLED=true
#
# this program would previously crash due to an unsigned integer wraparound
# error when resizing the heap profiler's array of tracked allocations. Now, the
# program runs to completion but prohibitevly slowly. It takes ~0.1s on my
# laptop without profiling and ~50s with profiling. Almost all of that time
# seems to be spent untracking allocations at the end of the program. It should
# be much faster.
#
# TODO(nick): Actually run this program in CI once we make the heap profiler
# scale & perform better.

count = 100_000
thing_size = 32

junk = []
for i in range(count):
    b1 = bytearray(thing_size)
    b2 = bytearray(2 * thing_size)
    b3 = bytearray(3 * thing_size)
    b4 = bytearray(4 * thing_size)
    t = (b1, b2, b3, b4)
    junk.append(t)

del junk
