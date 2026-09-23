#include "profiler_state.hpp"

#include "libdatadog_helpers.hpp"

#include <chrono>
#include <iostream>
#include <pthread.h>
#include <thread>
#include <unistd.h>

namespace Datadog {

ProfilerState&
ProfilerState::get()
{
    // Keep process-global profiler state alive until the OS reclaims it. Some embedders,
    // including uWSGI, run native atexit handlers before finalizing Python. Python
    // finalization can still invoke native sys.monitoring callbacks, so destroying this
    // state at native atexit would leave those callbacks accessing freed registries.
    static ProfilerState* const instance = new ProfilerState();
    return *instance;
}

bool
ProfilerState::init_profiles_dictionary()
{
    // Guard against double-initialization: profiles_dictionary must be empty before we create a new one.
    // This is guaranteed by call_once in start() for the initial call, and by release_profiles_dictionary()
    // being called before this in postfork_child().
    const std::lock_guard<std::mutex> lock(profiles_dictionary_mtx);
    if (profiles_dictionary.has_value()) {
        std::cerr << "profiles dictionary already initialized" << std::endl;
        return false;
    }

    auto result = ddprof::ProfileDictionary::create();
    if (!result->check_and_print()) {
        return false;
    }
    profiles_dictionary.emplace(result->take_value());
    profiles_dictionary.value()->set_error_policy(ddprof::ErrorPolicy::PrintOncePerOperation);

    return true;
}

std::optional<Borrow<ddprof::ProfileDictionary>>
ProfilerState::borrow_dictionary()
{
    std::unique_lock<std::mutex> lk(profiles_dictionary_mtx);
    if (!profiles_dictionary.has_value()) {
        return std::nullopt;
    }
    return Borrow<ddprof::ProfileDictionary>{ std::move(lk), *profiles_dictionary.value() };
}

void
ProfilerState::release_profiles_dictionary()
{
    const std::lock_guard<std::mutex> lock(profiles_dictionary_mtx);
    profiles_dictionary.reset();
}

void
ProfilerState::reset_key_caches()
{
    for (auto& entry : label_cache) {
        entry.store({}, std::memory_order_relaxed);
    }
}

void
ProfilerState::start()
{
    // init_flag_ is a std::once_flag. We intentionally do NOT reinitialise it after fork:
    // in the child process, postfork_child() re-creates the ProfileDictionary directly,
    // bypassing call_once. The once_flag therefore stays "already called" in the child,
    // which is correct — we don't want a second call to start() to re-run initialization.
    std::call_once(init_flag_, [this]() {
        // Initialize the profiles dictionary at process start
        if (!init_profiles_dictionary()) {
            return;
        }

        // TODO: If profile initialization fails after the dictionary is created,
        // call_once still records this initialization attempt as done. A follow-up
        // should either clean up partial state here or replace this with a
        // retryable initialization state machine.
        if (!profile_state.one_time_init(type_mask, max_nframes)) {
            return;
        }

        // Install fork handlers
        pthread_atfork([]() { ProfilerState::get().prefork(); },
                       []() { ProfilerState::get().postfork_parent(); },
                       []() { ProfilerState::get().postfork_child(); });

        // Register cleanup function to free resources on exit
        std::atexit([]() { ProfilerState::get().cleanup(); });

        // Set the global initialization flag
        initialized_.store(true, std::memory_order_release);
    });
}

void
ProfilerState::cleanup()
{
    // Mark the profiler unavailable before dropping profile state so callers
    // that check ddup_is_initialized() will not borrow an empty profile.
    initialized_.store(false, std::memory_order_release);

    // Clear the profile, decreasing the refcount on the ProfileDictionary
    profile_state.cleanup();

    // Decrease the refcount on the ProfileDictionary
    release_profiles_dictionary();
}

void
ProfilerState::prefork()
{
    // Cancel inflight uploads to prevent state leaking to children.
    upload_cancellation.cancel_inflight();

    // Keep cancelling and trying to acquire the lock until we succeed.
    while (!upload_lock.try_lock()) {
        upload_cancellation.cancel_inflight();
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    // upload_lock is now held - will be released in postfork_parent/child.

    upload_cancellation.prefork();

    // Lock the dictionary mutex so no thread is mid-intern when the child
    // reinitializes the dictionary. Acquired before profile_mtx to match
    // the temporal order of the sampling path (intern → collect).
    profiles_dictionary_mtx.lock();

    // Lock the profile mutex so the sampling thread cannot be mid-allocation
    // inside the CXX Profile::add_dictionary_sample path when the child resets profile state.
    // Both postfork_parent and postfork_child release it via unlock.
    profile_state.prefork();
}

void
ProfilerState::postfork_parent()
{
    profile_state.unlock();
    profiles_dictionary_mtx.unlock();
    upload_cancellation.postfork_parent();
    upload_lock.unlock();
}

void
ProfilerState::postfork_child()
{
    const bool was_initialized = initialized_.exchange(false, std::memory_order_acq_rel);

    // Step 1: Unlock all mutexes in reverse prefork order.
    // Child is single-threaded — no locks needed after this.
    profile_state.unlock();
    profiles_dictionary_mtx.unlock();
    upload_cancellation.postfork_child();
    upload_lock.unlock();

    // Step 2: Reset state that is invalid in the child.
    native_call_registry.postfork_child();
    profile_state.reset_after_fork();
    profiles_dictionary.reset();
    reset_key_caches();

    if (!was_initialized) {
        return;
    }

    // Step 3: Reinitialize. No locks — child is single-threaded.
    if (!init_profiles_dictionary()) {
        std::cerr << "failed to initialise profiles dictionary in child process, profiler will be disabled"
                  << std::endl;
        return;
    }
    if (!profile_state.reinit_after_fork()) {
        return;
    }
    initialized_.store(true, std::memory_order_release);
}

} // namespace Datadog
