#pragma once

#include "constants.hpp"
#include "libdatadog_helpers.hpp"
#include "profile.hpp"
#include "types.hpp"

#include <array>
#include <atomic>
#include <mutex>
#include <string>
#include <unordered_map>

namespace Datadog {

class Sample;

// ProfilerState is a singleton class that holds all Profiler "global" state.
// Consolidating it here makes lifecycle management (init, cleanup, fork handling) clearer.
// Note: this class does not start or stop threads. However, it installs fork handlers that
//   may stop threads through libdatadog helpers (abstracted away / out of our control).
//
// This class owns all shared mutable state for the profiler.
// When adding new state, consider whether it belongs here or in a specific component.
class ProfilerState
{
  public:
    using ExporterTagset = std::unordered_map<std::string, std::string>;

    // Singleton access
    static ProfilerState& get();

    // Lifecycle
    void start();
    void cleanup();
    void prefork();
    void postfork_parent();
    void postfork_child();

    // Query state
    bool is_initialized() const { return initialized_.load(std::memory_order_acquire); }

    // ========================================================================
    // Profiles Dictionary state
    // ========================================================================
    std::optional<ddog_prof_ProfilesDictionaryHandle> get_profiles_dictionary();
    void release_profiles_dictionary();

    // ========================================================================
    // Uploader configuration
    // ========================================================================
    std::string dd_env;
    std::string service;
    std::string version;
    std::string runtime{ g_runtime_name };
    std::string runtime_id;
    std::string process_id;
    std::string runtime_version;
    std::string profiler_version;
    std::string url{ "http://localhost:8126" };
    ExporterTagset user_tags{};
    std::string output_filename;
    uint64_t max_timeout_ms{ g_default_max_timeout_ms };
    std::string process_tags;

    // ========================================================================
    // Sample configuration
    // ========================================================================
    unsigned int max_nframes{ g_default_max_nframes };
    SampleType type_mask{ SampleType::All };
    size_t sample_pool_capacity{ g_default_sample_pool_capacity };

    // ========================================================================
    // Profile state
    // ========================================================================
    Profile profile_state{};
    bool timeline_enabled{ false };

    // ========================================================================
    // Upload state
    // ========================================================================
    std::mutex upload_lock{};
    // ddog_CancellationToken is documented as an opaque type, but we access .inner directly to
    // zero-initialize it: the C API provides no constructor, and the default value of .inner
    // is undefined. We check .inner != nullptr as a sentinel for "a token is in flight".
    std::atomic<ddog_CancellationToken> upload_cancel{ { .inner = nullptr } };
    std::atomic<uint64_t> upload_seq{ 0 };

    // ========================================================================
    // Interned string caches
    // ========================================================================
    static constexpr size_t kNumTagKeys = static_cast<size_t>(ExportTagKey::Length_);
    static constexpr size_t kNumLabelKeys = static_cast<size_t>(ExportLabelKey::Length_);
    std::array<std::atomic<ddog_prof_StringId2>, kNumTagKeys> tag_cache{};
    std::array<std::atomic<ddog_prof_StringId2>, kNumLabelKeys> label_cache{};
    ddog_prof_StringId2 cached_empty_string_id{ nullptr };

    // Internal helpers
    bool init_profiles_dictionary();
    bool init_interned_strings();
    void reset_key_caches();

  private:
    ProfilerState() = default;
    ~ProfilerState() = default;

    // Non-copyable, non-movable
    ProfilerState(const ProfilerState&) = delete;
    ProfilerState& operator=(const ProfilerState&) = delete;
    ProfilerState(ProfilerState&&) = delete;
    ProfilerState& operator=(ProfilerState&&) = delete;

    // Initialization state
    std::atomic<bool> initialized_{ false };
    std::once_flag init_flag_;

    // Profiles Dictionary handle
    std::atomic<ddog_prof_ProfilesDictionaryHandle> dict_handle_{ nullptr };
};

} // namespace Datadog
