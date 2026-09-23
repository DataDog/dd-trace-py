#pragma once

#include <cstdint>
#include <string_view>
#include <unordered_map>

#ifdef __cplusplus
extern "C"
{
#endif

    void ddup_set_profiler_settings_json(std::string_view settings_json);

    bool ddup_is_initialized();
    void ddup_start();
    void ddup_cleanup();

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
#ifdef __cplusplus
} // extern "C"
#endif
