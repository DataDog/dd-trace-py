#pragma once

#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>

extern "C"
{
  #include "datadog/profiling.h"
}

namespace Datadog {

// AIDEV-NOTE: ExporterManager wraps libdatadog's fork-safe async upload API.
// Unlike the old Uploader class which rebuilt the exporter for each upload,
// ExporterManager is created once and reused. It has a background worker thread
// that handles uploads asynchronously.
//
// Fork safety is handled by libdatadog internally:
// - prefork(): stops worker thread, captures inflight requests
// - postfork_parent(): restarts worker, re-queues inflight requests
// - postfork_child(): creates fresh worker, discards parent's inflight requests

class ExporterManager
{
  public:
    // Configuration - call these before init()
    static void set_env(std::string_view env);
    static void set_service(std::string_view service);
    static void set_version(std::string_view version);
    static void set_runtime(std::string_view runtime);
    static void set_runtime_id(std::string_view runtime_id);
    static void set_process_id();
    static void set_runtime_version(std::string_view runtime_version);
    static void set_profiler_version(std::string_view profiler_version);
    static void set_url(std::string_view url);
    static void set_tag(std::string_view key, std::string_view val);
    static void set_output_filename(std::string_view output_filename);
    static void set_max_timeout_ms(uint64_t max_timeout_ms);
    static void set_process_tags(std::string_view process_tags);

    // Initialize the manager - must be called after all config is set
    // Returns true on success, false on failure (error printed to stderr)
    static bool init();

    // Returns true if the manager has been initialized
    static bool is_initialized();

    // Upload the current profile (serializes and queues for async upload)
    // Returns true if queued successfully, false on error
    static bool upload();

    // Fork handlers - delegate to libdatadog's fork-safe API
    static void prefork();
    static void postfork_parent();
    static void postfork_child();

    // Shutdown - cleanly shut down the manager (drops the handle)
    // Call this before process exit to avoid data races in background threads
    static void shutdown();

  private:
    // Configuration storage
    static inline std::string dd_env;
    static inline std::string service;
    static inline std::string version;
    static inline std::string runtime;
    static inline std::string runtime_id;
    static inline std::string process_id;
    static inline std::string runtime_version;
    static inline std::string profiler_version;
    static inline std::string url{ "http://localhost:8126" };
    static inline std::unordered_map<std::string, std::string> user_tags;
    static inline std::string output_filename;
    static inline uint64_t max_timeout_ms{ 10'000 };
    static inline std::string process_tags;

    // Manager state
    static inline ddog_prof_Handle_ExporterManager manager{ .inner = nullptr };
    static inline bool initialized{ false };
    static inline std::mutex manager_mutex;

    // Helper to create the exporter with current config
    static std::optional<ddog_prof_ProfileExporter> create_exporter();

    // Helper to export profile to file (when output_filename is set)
    static bool export_to_file(ddog_prof_EncodedProfile& encoded,
                               std::string_view internal_metadata_json,
                               uint64_t upload_seq);
};

} // namespace Datadog
