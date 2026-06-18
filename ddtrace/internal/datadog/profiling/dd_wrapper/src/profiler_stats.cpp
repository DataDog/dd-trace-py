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
    heap_tracker_size = std::nullopt;
    asyncio_task_count = std::nullopt;
    greenlet_count = std::nullopt;
    sample_capture_cpu_time_us = 0;
    gc_snapshot_count = 0;
    gc_snapshot_wall_time_us = 0;
    gc_gc_stats_time_us = 0;
    gc_get_objects_time_us = 0;
    gc_type_scan_time_us = 0;
    gc_survivor_update_time_us = 0;
    gc_referrers_time_us = 0;
    gc_serialize_time_us = 0;
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

void
Datadog::ProfilerStats::set_asyncio_task_count(size_t count)
{
    asyncio_task_count = count;
}

std::optional<size_t>
Datadog::ProfilerStats::get_asyncio_task_count() const
{
    return asyncio_task_count;
}

void
Datadog::ProfilerStats::set_greenlet_count(size_t count)
{
    greenlet_count = count;
}

std::optional<size_t>
Datadog::ProfilerStats::get_greenlet_count() const
{
    return greenlet_count;
}

void
Datadog::ProfilerStats::add_sample_capture_cpu_time_us(size_t cpu_time_us)
{
    sample_capture_cpu_time_us += cpu_time_us;
}

size_t
Datadog::ProfilerStats::get_sample_capture_cpu_time_us() const
{
    return sample_capture_cpu_time_us;
}

void
Datadog::ProfilerStats::add_gc_snapshot_timing(const GCSnapshotTiming& t)
{
    gc_snapshot_count += 1;
    gc_snapshot_wall_time_us += t.wall_us;
    gc_gc_stats_time_us += t.gc_stats_us;
    gc_get_objects_time_us += t.get_objects_us;
    gc_type_scan_time_us += t.type_scan_us;
    gc_survivor_update_time_us += t.survivor_update_us;
    gc_referrers_time_us += t.referrers_us;
    gc_serialize_time_us += t.serialize_us;
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

    auto maybe_fast_copy_enabled = get_fast_copy_memory_enabled();
    if (maybe_fast_copy_enabled) {
        internal_metadata_json += R"("fast_copy_memory_enabled": )";
        internal_metadata_json += *maybe_fast_copy_enabled ? "true" : "false";
        internal_metadata_json += ",";
    }

    auto maybe_heap_tracker_count = get_heap_tracker_size();
    if (maybe_heap_tracker_count) {
        internal_metadata_json += R"("heap_tracker_count": )";
        append_to_string(internal_metadata_json, *maybe_heap_tracker_count);
        internal_metadata_json += ",";
    }

    auto maybe_asyncio_task_count = get_asyncio_task_count();
    if (maybe_asyncio_task_count) {
        internal_metadata_json += R"("asyncio_task_count": )";
        append_to_string(internal_metadata_json, *maybe_asyncio_task_count);
        internal_metadata_json += ",";
    }

    auto maybe_greenlet_count = get_greenlet_count();
    if (maybe_greenlet_count) {
        internal_metadata_json += R"("greenlet_count": )";
        append_to_string(internal_metadata_json, *maybe_greenlet_count);
        internal_metadata_json += ",";
    }

    internal_metadata_json += R"("copy_memory_error_count": )";
    append_to_string(internal_metadata_json, copy_memory_error_count);
    internal_metadata_json += ",";

    internal_metadata_json += R"("sample_capture_cpu_time_us": )";
    append_to_string(internal_metadata_json, sample_capture_cpu_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_snapshot_count": )";
    append_to_string(internal_metadata_json, gc_snapshot_count);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_snapshot_wall_time_us": )";
    append_to_string(internal_metadata_json, gc_snapshot_wall_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_gc_stats_time_us": )";
    append_to_string(internal_metadata_json, gc_gc_stats_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_get_objects_time_us": )";
    append_to_string(internal_metadata_json, gc_get_objects_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_type_scan_time_us": )";
    append_to_string(internal_metadata_json, gc_type_scan_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_survivor_update_time_us": )";
    append_to_string(internal_metadata_json, gc_survivor_update_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_referrers_time_us": )";
    append_to_string(internal_metadata_json, gc_referrers_time_us);
    internal_metadata_json += ",";

    internal_metadata_json += R"("gc_serialize_time_us": )";
    append_to_string(internal_metadata_json, gc_serialize_time_us);

    internal_metadata_json += "}";

    return internal_metadata_json;
}
