import inspect
import tracemalloc

# TODO(nick): we import _memalloc explicitly so that we can
# control exactly when a heap profile is collected. This makes
# it viable to make concrete assertions about what's in the heap
# profile. Ideally we'd test our profiler end-to-end, but it's
# a bit painful to do so for these accuracy assertions
from ddtrace.profiling.collector import _memalloc


# one, two, three, and four exist to give us distinct things
# we can find in the profile without depending on something
# like the line number at which an allocation happens
def one(size):
    return bytearray(size)


def two(size):
    return bytearray(size)


def three(size):
    return bytearray(size)


def four(size):
    return bytearray(size)


class HeapInfo:
    def __init__(self, count, size):
        self.count = count
        self.size = size


def get_heap_info(heap, funcs):
    got = {}
    for (frames, _, _), size in heap:
        func = frames[0].function_name
        if func in funcs:
            v = got.get(func, HeapInfo(0, 0))
            v.count += 1
            v.size += size
            got[func] = v
    return got


source_to_func = {}

for f in (one, two, three, four):
    file = inspect.getsourcefile(f)
    line = inspect.getsourcelines(f)[1] + 1
    source_to_func[str(file) + str(line)] = f.__name__


for sample_rate in (1, 8, 512, 1024, 32 * 1024):
    print(f"==== sample size {sample_rate} ====")
    _memalloc.start(32, 1000, sample_rate)

    # tracemalloc lets us get ground truth on how many allocations there were
    tracemalloc.start()

    junk = []
    for i in range(1000):
        size = 256
        junk.append(one(size))
        junk.append(two(2 * size))
        junk.append(three(3 * size))
        junk.append(four(4 * size))

    heap = _memalloc.heap()
    stats = tracemalloc.take_snapshot().statistics("traceback")

    tracemalloc.stop()
    # Important: stop _memalloc _after_ tracemalloc. Need to remove allocator
    # hooks in LIFO order
    _memalloc.stop()

    # actual_sizes tracks the number of bytes allocated for the functions we're
    # tracking, according to tracemalloc
    actual_sizes = {}
    for stat in stats:
        f = stat.traceback[0]
        key = f.filename + str(f.lineno)
        if key in source_to_func:
            actual_sizes[source_to_func[key]] = stat.size
    actual_total = sum(actual_sizes.values())

    del junk

    sizes = get_heap_info(heap, {"one", "two", "three", "four"})

    # TODO(nick): total seems to be pretty consistently larger than
    # actual_total, I think because the subset of allocations I'm picking get
    # "scaled" sizes based on all the allocations that happened in-between
    # sampled allocations, which includes growing the junk array.
    total = sum(v.size for v in sizes.values())
    print(f"observed total: {total} actual total: {actual_total}")

    print("func\tcount\tsize\tactual\tproportion\tactual\terror")
    for func in ("one", "two", "three", "four"):
        got = sizes[func]
        actual_size = actual_sizes[func]

        # Relative portion of the bytes in the profile for this function
        # out of the functions we're interested in
        rel = got.size / total
        actual_rel = actual_size / actual_total

        # TODO(nick): make actual assertions about accuracy. In general
        # we expect it to go down as sampling rate goes up. For now, at least
        # print what we get.
        print(f"{func}\t{got.count}\t{got.size}\t{actual_size}\t{rel:6f}\t{actual_rel:6f}\t{abs(rel - actual_rel):6f}")
