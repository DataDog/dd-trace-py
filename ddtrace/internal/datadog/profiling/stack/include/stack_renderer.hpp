#pragma once

#include <cstdint>
#include <string>
#include <string_view>

#include "python_headers.hpp"

#include "dd_wrapper/include/sample.hpp"

#include "echion/frame.h"
#include "echion/timing.h"

namespace Datadog {

enum class MetricType : std::uint8_t
{
    Time,
    Memory
};

namespace internal {

struct PtrPair
{
    void* a;
    void* b;
};

struct PtrPairHash
{
    // Hash combining using the golden ratio constant (2^64 / phi).
    // This is a standard technique similar to boost::hash_combine.
    inline size_t operator()(const PtrPair& p) const noexcept
    {
        uintptr_t h1 = reinterpret_cast<uintptr_t>(p.a);
        uintptr_t h2 = reinterpret_cast<uintptr_t>(p.b);
        return h1 ^ (h2 * 0x9e3779b97f4a7c15ULL);
    }
};

struct PtrPairEq
{
    inline bool operator()(const PtrPair& x, const PtrPair& y) const noexcept { return x.a == y.a && x.b == y.b; }
};

} // namespace internal

struct ThreadState
{
    // Current thread info.  Keeping one instance of this per StackRenderer is sufficient because the renderer visits
    // threads one at a time.
    // The only time this information is revealed is when the sampler observes a thread. When the sampler goes on to
    // process tasks, it needs to place thread-level information in the Sample.
    uintptr_t id = 0;
    unsigned long native_id = 0;
    std::string name;
    microsecond_t wall_time_ns = 0;
    microsecond_t cpu_time_ns = 0;
    int64_t now_time_ns = 0;
};

class StackRenderer
{
    Sample* sample = nullptr;
    ThreadState thread_state = {};

    // Caches for interned strings and function IDs. These are used to avoid
    // re-interning the same strings and function IDs multiple times (even though libdatadog
    // deduplicates entries, keeping track of which items have been interned is faster than
    // trying to re-intern them).
    std::unordered_map<StringTable::Key, string_id> string_id_cache;
    std::unordered_map<internal::PtrPair, function_id, internal::PtrPairHash, internal::PtrPairEq> function_id_cache;

    // Whether task name has been pushed for the current sample. Whenever
    // the sample is created, this has to be reset.
    bool pushed_task_name = false;

  public:
    StackRenderer();
    void render_thread_begin(PyThreadState* tstate,
                             std::string_view name,
                             microsecond_t wall_time_us,
                             uintptr_t thread_id,
                             unsigned long native_id);
    void render_task_begin(const std::string& task_name, bool on_cpu);
    void render_frame(Frame& frame);
    void render_cpu_time(uint64_t cpu_time_us);
    void render_stack_end();

    // Clear caches after fork to avoid using stale interned string/function IDs
    void postfork_child();
};

} // namespace Datadog
