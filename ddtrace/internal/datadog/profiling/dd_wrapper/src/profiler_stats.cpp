#include "profiler_stats.hpp"

#include <charconv>

namespace {

void
append_to_string(std::string& s, size_t value)
{
    char buf[128];
    auto [ptr, ec] = std::to_chars(std::begin(buf), std::end(buf), value);
    s.append(buf, ptr);
}

} // namespace

void
Datadog::ProfilerStats::increment_sampling_event_count(size_t k_sampling_event_count)
{
    sampling_event_count += k_sampling_event_count;
}

size_t
Datadog::ProfilerStats::get_sampling_event_count() const
{
    return sampling_event_count;
}

void
Datadog::ProfilerStats::increment_sample_count(size_t k_sample_count)
{
    sample_count += k_sample_count;
}

size_t
Datadog::ProfilerStats::get_sample_count() const
{
    return sample_count;
}

void
Datadog::ProfilerStats::reset_state()
{
    sample_count = 0;
    sampling_event_count = 0;
    sampling_interval_us = std::nullopt;
    string_table_count = std::nullopt;
    string_table_ephemeral_count = std::nullopt;
    copy_memory_error_count = 0;
    // fast_copy_memory_enabled is intentionally not reset: it reflects a static configuration
}

void
Datadog::ProfilerStats::set_fast_copy_memory_enabled(bool enabled)
{
    fast_copy_memory_enabled = enabled;
}

std::optional<bool>
Datadog::ProfilerStats::get_fast_copy_memory_enabled() const
{
    return fast_copy_memory_enabled;
}

void
Datadog::ProfilerStats::add_copy_memory_error_count(size_t count)
{
    copy_memory_error_count += count;
}

size_t
Datadog::ProfilerStats::get_copy_memory_error_count() const
{
    return copy_memory_error_count;
}

void
Datadog::ProfilerStats::set_sampling_interval_us(size_t interval_us)
{
    sampling_interval_us = interval_us;
}

std::optional<size_t>
Datadog::ProfilerStats::get_sampling_interval_us() const
{
    return sampling_interval_us;
}

void
Datadog::ProfilerStats::set_string_table_count(size_t count)
{
    string_table_count = count;
}

std::optional<size_t>
Datadog::ProfilerStats::get_string_table_count() const
{
    return string_table_count;
}

void
Datadog::ProfilerStats::set_string_table_ephemeral_count(size_t count)
{
    string_table_ephemeral_count = count;
}

std::optional<size_t>
Datadog::ProfilerStats::get_string_table_ephemeral_count() const
{
    return string_table_ephemeral_count;
}

void
Datadog::ProfilerStats::set_heap_tracker_size(size_t count)
{
    heap_tracker_size = count;
}

std::optional<size_t>
Datadog::ProfilerStats::get_heap_tracker_size() const
{
    return heap_tracker_size;
}

std::string
Datadog::ProfilerStats::get_internal_metadata_json()
{
    std::string internal_metadata_json;
    internal_metadata_json.reserve(128);

    internal_metadata_json += "{";

    auto maybe_sampling_interval = get_sampling_interval_us();
    if (maybe_sampling_interval) {
        internal_metadata_json += R"("sampling_interval_us": )";
        append_to_string(internal_metadata_json, *maybe_sampling_interval);
        internal_metadata_json += ",";
    }

    auto maybe_string_table_count = get_string_table_count();
    if (maybe_string_table_count) {
        internal_metadata_json += R"("string_table_count": )";
        append_to_string(internal_metadata_json, *maybe_string_table_count);
        internal_metadata_json += ",";
    }

    auto maybe_string_table_ephemeral_count = get_string_table_ephemeral_count();
    if (maybe_string_table_ephemeral_count) {
        internal_metadata_json += R"("string_table_ephemeral_count": )";
        append_to_string(internal_metadata_json, *maybe_string_table_ephemeral_count);
        internal_metadata_json += ",";
    }

    internal_metadata_json += R"("sample_count": )";
    append_to_string(internal_metadata_json, sample_count);
    internal_metadata_json += ",";

    internal_metadata_json += R"("sampling_event_count": )";
    append_to_string(internal_metadata_json, sampling_event_count);
    internal_metadata_json += ",";

    internal_metadata_json += R"("copy_memory_error_count": )";
    append_to_string(internal_metadata_json, copy_memory_error_count);

    auto maybe_fast_copy_enabled = get_fast_copy_memory_enabled();
    if (maybe_fast_copy_enabled) {
        internal_metadata_json += ",";
        internal_metadata_json += R"("fast_copy_memory_enabled": )";
        internal_metadata_json += *maybe_fast_copy_enabled ? "true" : "false";
    }

    auto maybe_heap_tracker_count = get_heap_tracker_size();
    if (maybe_heap_tracker_count) {
        internal_metadata_json += ",";
        internal_metadata_json += R"("heap_tracker_count": )";
        append_to_string(internal_metadata_json, *maybe_heap_tracker_count);
    }

    internal_metadata_json += "}";

    return internal_metadata_json;
}
