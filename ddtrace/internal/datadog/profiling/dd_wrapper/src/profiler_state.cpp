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

    try {
        profiles_dictionary.emplace(ddprof::ProfilesDictionary::create());
    } catch (const std::exception& err) {
        std::cerr << "could not initialise CXX profiles dictionary: " << err.what() << std::endl;
        return false;
    }

    return true;
}

ddprof::ProfilesDictionary*
ProfilerState::get_profiles_dictionary()
{
    const std::lock_guard<std::mutex> lock(profiles_dictionary_mtx);
    if (!profiles_dictionary.has_value()) {
        return nullptr;
    }
    return &profiles_dictionary.value().operator*();
}

void
ProfilerState::release_profiles_dictionary()
{
    const std::lock_guard<std::mutex> lock(profiles_dictionary_mtx);
    profiles_dictionary.reset();
}

bool
ProfilerState::init_interned_strings()
{
    auto* dict = get_profiles_dictionary();
    if (dict == nullptr) {
        return false;
    }

    try {
        // Intern the empty string, which is used frequently.
        cached_empty_string_id = dict->insert_string("");
    } catch (const std::exception& err) {
        std::cerr << "Error interning empty string: " << err.what() << std::endl;
        return false;
    }

    return true;
}

void
ProfilerState::reset_key_caches()
{
    for (auto& entry : tag_cache) {
        entry.store({ nullptr }, std::memory_order_relaxed);
    }
    for (auto& entry : label_cache) {
        entry.store({ nullptr }, std::memory_order_relaxed);
    }
    cached_empty_string_id = { nullptr };
}

void
ProfilerState::start()
{
    // init_flag_ is a std::once_flag. We intentionally do NOT reinitialise it after fork:
    // in the child process, postfork_child() re-creates the Profiles Dictionary directly,
    // bypassing call_once. The once_flag therefore stays "already called" in the child,
    // which is correct — we don't want a second call to start() to re-run initialization.
    std::call_once(init_flag_, [this]() {
        // Initialize the profiles dictionary at process start
        if (!init_profiles_dictionary()) {
            return;
        }

        // Initialize cached interned strings (must happen after profiles dictionary is created)
        if (!init_interned_strings()) {
            return;
        }

        // Initialize the Profile object
        profile_state.one_time_init(type_mask, max_nframes);

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
    // Clear the profile, decreasing the refcount on the Profiles Dictionary
    profile_state.cleanup();

    // Decrease the refcount on the Profiles Dictionary
    release_profiles_dictionary();
}

void
ProfilerState::prefork()
{
    auto cancel_current_upload = [this]() {
        const std::lock_guard<std::mutex> cancel_lock(upload_cancel_mtx);
        if (upload_cancel.has_value()) {
            (*upload_cancel)->cancel();
            upload_cancel.reset();
        }
    };

    // Cancel inflight uploads to prevent state leaking to children.
    cancel_current_upload();

    // Keep cancelling and trying to acquire the lock until we succeed.
    while (!upload_lock.try_lock()) {
        cancel_current_upload();
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    // upload_lock is now held - will be released in postfork_parent/child.

    // Hold upload_cancel_mtx across fork so it cannot be locked by another thread
    // in the child process. postfork_parent releases it; postfork_child replaces it.
    upload_cancel_mtx.lock();
    if (upload_cancel.has_value()) {
        (*upload_cancel)->cancel();
        upload_cancel.reset();
    }

    // Lock the profile mutex so the sampling thread cannot be mid-allocation
    // inside ddog_prof_Profile_add2 when the child calls ddog_prof_Profile_drop.
    // postfork_parent releases it via unlock; postfork_child releases it
    // via placement-new reinit of profile_mtx (which implicitly creates a fresh
    // unlocked mutex, consistent with every other mutex's postfork path).
    profile_state.prefork();
}

void
ProfilerState::postfork_parent()
{
    profile_state.postfork_parent();
    upload_cancel_mtx.unlock();
    upload_lock.unlock();
}

void
ProfilerState::postfork_child()
{
    // profile_mtx was locked in prefork; ensure postfork_child is called on
    // every exit path to unlock it.
    // We need to call this at the end of the function because the Sampling Thread
    // needs the ProfilerState to be consistent and waits on the profile_mtx that
    // postfork_child releases.
    struct ProfileGuard
    {
        ProfilerState& self;
        ~ProfileGuard() { self.profile_state.postfork_child(); }
    } guard{ *this };

    // Re-init the mutexes (placement-new to avoid UB with mutexes in undefined state after fork)
    new (&upload_lock) std::mutex();
    new (&upload_cancel_mtx) std::mutex();
    upload_cancel.reset();

    // Re-init the native call registry mutex (data is preserved so forked
    // children can still see native frames from the parent's warmup phase)
    native_call_registry.postfork_child();

    // Free our copy of the Profiles Dictionary - its String IDs refer to memory
    // that doesn't exist in the child process
    release_profiles_dictionary();

    // Reset all caches that depend on the Profiles Dictionary
    reset_key_caches();

    // Re-initialize the Profiles Dictionary in the child process
    if (!init_profiles_dictionary()) {
        std::cerr << "failed to initialise profiles dictionary in child process, profiler will be disabled"
                  << std::endl;
        initialized_.store(false, std::memory_order_release);
        return;
    }

    // Initialize cached interned strings with the new Profiles Dictionary
    if (!init_interned_strings()) {
        std::cerr << "failed to initialise interned strings in child process, profiler will be disabled" << std::endl;
        initialized_.store(false, std::memory_order_release);
        return;
    }
}

} // namespace Datadog
