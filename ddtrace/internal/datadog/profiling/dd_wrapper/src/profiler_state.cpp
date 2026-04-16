#include "profiler_state.hpp"

#include "libdatadog_helpers.hpp"

#include <chrono>
#include <iostream>
#include <pthread.h>
#include <thread>
#include <unistd.h>
#include <vector>

extern "C"
{
#include <datadog/profiling.h>
}

namespace Datadog {

ProfilerState&
ProfilerState::get()
{
    static ProfilerState instance;
    return instance;
}

bool
ProfilerState::init_profiles_dictionary()
{
    // Guard against double-initialization: dict_handle_ must be null before we create a new one.
    // This is guaranteed by call_once in start() for the initial call, and by release_profiles_dictionary()
    // being called before this in postfork_child().
    if (dict_handle_.load(std::memory_order_acquire) != nullptr) {
        std::cerr << "profiles dictionary already initialized" << std::endl;
        return false;
    }

    ddog_prof_ProfilesDictionaryHandle temp = nullptr;
    auto result = ddog_prof_ProfilesDictionary_new(&temp);
    if (result.flags) {
        std::cerr << "could not initialise profiles dictionary: " << result.err << std::endl;
        return false;
    }

    dict_handle_.store(temp, std::memory_order_release);
    return true;
}

std::optional<ddog_prof_ProfilesDictionaryHandle>
ProfilerState::get_profiles_dictionary()
{
    auto handle = dict_handle_.load(std::memory_order_acquire);
    if (handle == nullptr) {
        return std::nullopt;
    }
    return handle;
}

void
ProfilerState::release_profiles_dictionary()
{
    // Atomically swap out the handle before dropping, so concurrent callers of
    // get_profiles_dictionary() see nullptr rather than a pointer to freed memory.
    ddog_prof_ProfilesDictionaryHandle temp = dict_handle_.exchange(nullptr, std::memory_order_acq_rel);
    if (temp != nullptr) {
        ddog_prof_ProfilesDictionary_drop(&temp);
    }
}

bool
ProfilerState::init_interned_strings()
{
    auto maybe_dict = get_profiles_dictionary();
    if (!maybe_dict) {
        return false;
    }

    // Intern the empty string, which is used frequently
    ddog_prof_StringId2 string_id;
    auto result = ddog_prof_ProfilesDictionary_insert_str(
      &string_id, maybe_dict.value(), to_slice(""), ddog_prof_Utf8Option::DDOG_PROF_UTF8_OPTION_CONVERT_LOSSY);

    if (result.flags) {
        std::cerr << "Error interning empty string: " << result.err << std::endl;
        return false;
    }
    cached_empty_string_id = string_id;

    return true;
}

void
ProfilerState::reset_key_caches()
{
    for (auto& entry : tag_cache) {
        entry.store(nullptr, std::memory_order_relaxed);
    }
    for (auto& entry : label_cache) {
        entry.store(nullptr, std::memory_order_relaxed);
    }
    cached_empty_string_id = nullptr;
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
    // Drop the cached exporter
    if (ddog_exporter.inner != nullptr) {
        ddog_prof_Exporter_drop(&ddog_exporter);
        ddog_exporter = { .inner = nullptr };
    }

    // Clear the profile, decreasing the refcount on the Profiles Dictionary
    profile_state.cleanup();

    // Decrease the refcount on the Profiles Dictionary
    release_profiles_dictionary();
}

void
ProfilerState::prefork()
{
    // Cancel inflight uploads to prevent state leaking to children
    auto current_cancel = upload_cancel.exchange({ .inner = nullptr });
    if (current_cancel.inner != nullptr) {
        ddog_CancellationToken_cancel(&current_cancel);
        ddog_CancellationToken_drop(&current_cancel);
    }

    // Keep cancelling and trying to acquire the lock until we succeed
    while (!upload_lock.try_lock()) {
        current_cancel = upload_cancel.exchange({ .inner = nullptr });
        if (current_cancel.inner != nullptr) {
            ddog_CancellationToken_cancel(&current_cancel);
            ddog_CancellationToken_drop(&current_cancel);
        }
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    // upload_lock is now held - will be released in postfork_parent/child

    // Drop the cached exporter before fork so the child doesn't inherit stale
    // Rust state (tokio runtime, TLS, connection pool). Dropping here — in the
    // parent, under lock, with no upload in flight — is safe. Both parent and
    // child will lazily recreate the exporter on the next upload.
    if (ddog_exporter.inner != nullptr) {
        ddog_prof_Exporter_drop(&ddog_exporter);
        ddog_exporter = { .inner = nullptr };
    }
}

void
ProfilerState::postfork_parent()
{
    upload_lock.unlock();
}

void
ProfilerState::postfork_child()
{
    // Re-init the mutex (placement-new to avoid UB with mutex in undefined state after fork)
    new (&upload_lock) std::mutex();

    // The cached exporter was already dropped in prefork(), so ddog_exporter.inner
    // is nullptr here. Nothing to do.

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

    // Reset the profile state
    profile_state.postfork_child();
}

ddog_prof_ProfileExporter*
ProfilerState::get_or_create_exporter(std::string& errmsg)
{
    // Fast path: exporter already exists
    if (ddog_exporter.inner != nullptr) {
        return &ddog_exporter;
    }

    // Build tags for the exporter
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    std::vector<std::string> reasons{};
    const std::vector<std::pair<ExportTagKey, std::string_view>> tag_data = {
        { ExportTagKey::dd_env, dd_env },
        { ExportTagKey::service, service },
        { ExportTagKey::version, version },
        { ExportTagKey::language, g_language_name },
        { ExportTagKey::runtime, runtime },
        { ExportTagKey::runtime_id, runtime_id },
        { ExportTagKey::runtime_version, runtime_version },
        { ExportTagKey::profiler_version, profiler_version },
        { ExportTagKey::process_id, process_id }
    };

    for (const auto& [tag, data] : tag_data) {
        if (!data.empty()) {
            std::string tag_errmsg;
            if (!add_tag(tags, tag, data, tag_errmsg)) {
                reasons.push_back(std::string(to_string(tag)) + ": " + tag_errmsg);
            }
        }
    }

    // Add user-defined tags
    for (const auto& tag : user_tags) {
        std::string tag_errmsg;
        if (!add_tag(tags, tag.first, tag.second, tag_errmsg)) {
            reasons.push_back(std::string(tag.first) + ": " + tag_errmsg);
        }
    }

    if (!reasons.empty()) {
        ddog_Vec_Tag_drop(tags);
        errmsg = "Error initializing exporter, missing or bad configuration: ";
        for (size_t i = 0; i < reasons.size(); ++i) {
            if (i > 0) {
                errmsg += ", ";
            }
            errmsg += reasons[i];
        }
        return nullptr;
    }

    // Create the exporter
    ddog_prof_ProfileExporter_Result res =
      ddog_prof_Exporter_new(to_slice(g_library_name),
                             to_slice(profiler_version),
                             to_slice(g_language_name),
                             &tags,
                             ddog_prof_Endpoint_agent(to_slice(url), max_timeout_ms, /*use_system_resolver=*/false));
    ddog_Vec_Tag_drop(tags);

    if (res.tag == DDOG_PROF_PROFILE_EXPORTER_RESULT_ERR_HANDLE_PROFILE_EXPORTER) {
        auto& err = res.err;
        errmsg = err_to_msg(&err, "Error initializing exporter");
        ddog_Error_drop(&err);
        return nullptr;
    }

    ddog_exporter = res.ok;
    return &ddog_exporter;
}

} // namespace Datadog
