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
// threading.current_thread().name from there can re-enter the eval loop and
// release the GIL, which lets other threads observe partially-constructed
// interpreter state and crashes the process. That call was removed in #16405,
// which left allocation samples labelled with str(thread_id) instead of a name.
// This registry gives the hook a name to report without touching the
// interpreter.
class ThreadNameRegistry
{
  public:
    // Threads are registered as they start and dropped as they finish, so the
    // registry tracks live threads only. Cap it anyway: an application that
    // churns through threads while the profiler is stopped, or one whose
    // threads exit without running our teardown hook, must not grow it without
    // bound.
    static constexpr size_t max_thread_names = 8192;

    ThreadNameRegistry() = default;
    ~ThreadNameRegistry() = default;

    ThreadNameRegistry(ThreadNameRegistry const&) = delete;
    ThreadNameRegistry& operator=(ThreadNameRegistry const&) = delete;

    // Records `name` for `thread_id`, replacing any previous name. Call this
    // only from a safe point, never from inside an allocator hook: it takes the
    // registry's write lock and allocates.
    void register_name(int64_t thread_id, std::string_view name);

    // Drops the name recorded for `thread_id`.
    void unregister_name(int64_t thread_id);

    // Returns the name recorded for `thread_id`, or an empty view if none is
    // known.
    //
    // Safe to call from an allocator hook: it never calls into the interpreter,
    // and it takes the read lock with try_lock rather than blocking, because
    // register_name allocates while holding the write lock and that allocation
    // can itself be sampled on the same thread. Reporting no name is the right
    // outcome for that rare race; blocking would deadlock.
    //
    // The returned view points at thread-local storage, so it stays valid until
    // the next lookup on the calling thread. Copy it if you need it longer.
    std::string_view lookup(int64_t thread_id);

    void reset();

    void postfork_child();

    size_t size() const;

  private:
    mutable std::shared_mutex mtx;
    std::unordered_map<int64_t, std::string> thread_names;
};

} // namespace Datadog
