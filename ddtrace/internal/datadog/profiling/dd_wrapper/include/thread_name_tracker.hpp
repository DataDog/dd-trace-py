#pragma once

#include <cstdint>
#include <shared_mutex>
#include <string>
#include <string_view>
#include <unordered_map>

namespace Datadog {

// Thread names, keyed by Python thread id, recorded from Python at safe points.
//
// The memory profiler samples from inside CPython's allocator hook. Reading
// thread names from there can re-enter the interpreter and release the GIL,
// which is not safe.
// This registry gives hooks a name to report without touching the interpreter.
class ThreadNameRegistry
{
  public:
    // Maximum number of thread names to record.
    // Past this limit, new names are dropped.
    static constexpr size_t max_thread_names = 8192;

    ThreadNameRegistry() = default;
    ~ThreadNameRegistry() = default;

    ThreadNameRegistry(ThreadNameRegistry const&) = delete;
    ThreadNameRegistry& operator=(ThreadNameRegistry const&) = delete;

    // Records name for thread_id
    void register_name(int64_t thread_id, std::string_view name);

    // Drops the name recorded for a thread_id
    void unregister_name(int64_t thread_id);

    // Replaces the recorded names with names, which should list the threads that are alive.
    void replace_all(std::unordered_map<int64_t, std::string_view> const& names);

    // Returns the name recorded for thread_id, or an empty view if none is known.
    std::string_view lookup(int64_t thread_id);

    void reset();

    void prefork();
    void postfork_parent();
    void postfork_child();

    size_t size() const;

  private:
    mutable std::shared_mutex mtx;
    std::unordered_map<int64_t, std::string> thread_names;
};

} // namespace Datadog
