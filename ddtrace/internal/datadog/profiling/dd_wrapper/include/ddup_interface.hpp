#pragma once

#include <cstdint>
#include <string_view>
#include <unordered_map>

// Forward declaration of Python types.
// We avoid including Python.h in this public C++ header because CPython headers
// use old-style casts and our build treats old-style casts as errors. Keep
// Python includes in implementation files when full API access is required.
// NOLINTBEGIN(bugprone-reserved-identifier) -- must match CPython's struct names
struct _traceback;
typedef struct _traceback PyTracebackObject;
// NOLINTEND(bugprone-reserved-identifier)

#ifdef __cplusplus
extern "C"
{
#endif

    void ddup_config_env(std::string_view dd_env);
    void ddup_config_service(std::string_view service);
    void ddup_config_version(std::string_view version);
    void ddup_config_runtime_version(std::string_view runtime_version);
    void ddup_config_runtime(std::string_view runtime);
    void ddup_config_profiler_version(std::string_view profiler_version);
    void ddup_config_url(std::string_view url);
    void ddup_config_max_nframes(int max_nframes);
    void ddup_config_timeline(bool enable);
    void ddup_config_output_filename(std::string_view filename);
    void ddup_config_sample_pool_capacity(uint64_t capacity);
    void ddup_config_set_max_timeout_ms(uint64_t max_timeout_ms);
    void ddup_config_process_tags(std::string_view process_tags);

    void ddup_config_user_tag(std::string_view key, std::string_view val);
    void ddup_config_sample_type(unsigned int type);
    void ddup_set_profiler_settings_json(std::string_view settings_json);

    bool ddup_is_initialized();
    void ddup_start();
    void ddup_cleanup();
    void ddup_set_runtime_id(std::string_view runtime_id);
    void ddup_set_process_id();

    // Pass by value is intentional: the map may be modified concurrently by other threads,
    // so we take a copy to avoid data races while iterating.
    void ddup_profile_set_endpoints(
      // NOLINTNEXTLINE(performance-unnecessary-value-param)
      std::unordered_map<int64_t, std::string_view> span_ids_to_endpoints);

    // Pass by value is intentional: the map may be modified concurrently by other threads,
    // so we take a copy to avoid data races while iterating.
    void ddup_profile_add_endpoint_counts(
      // NOLINTNEXTLINE(performance-unnecessary-value-param)
      std::unordered_map<std::string_view, int64_t> trace_endpoints_to_counts);

    bool ddup_upload();

    // GC monitor controls
    void ddup_start_gc_monitor(uint64_t interval_ms,
                               int survivor_threshold,
                               int top_n,
                               bool referrers_enabled,
                               int max_depth);
    void ddup_stop_gc_monitor();
#ifdef __cplusplus
} // extern "C"
#endif
