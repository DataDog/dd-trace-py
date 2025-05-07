#include "sample.hpp"

#include "code_provenance.hpp"

#include <algorithm>
#include <chrono>
#include <string_view>
#include <thread>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif

Datadog::internal::StringArena::StringArena()
{
    chunks.emplace_back();
    chunks.back().reserve(Datadog::internal::StringArena::DEFAULT_SIZE);
}

void
Datadog::internal::StringArena::reset()
{
    // Free chunks. Keep the first one around so it's easy to reuse this without
    // needing new allocations every time. We can completely drop it to get rid
    // of everything
    // TODO - we could consider keeping more around if it's not too costly.
    // The goal is to not retain more than we need _on average_. If we have
    // mostly small samples and then a rare huge one, we can end up with
    // all samples in our pool using as much memory as the largets ones we've seen
    chunks.front().clear();
    chunks.erase(++chunks.begin(), chunks.end());
}

std::string_view
Datadog::internal::StringArena::insert(std::string_view s)
{
    auto chunk = &chunks.back();
    if ((chunk->capacity() - chunk->size()) < s.size()) {
        chunk = &chunks.emplace_back();
        chunk->reserve(std::max(s.size(), Datadog::internal::StringArena::DEFAULT_SIZE));
    }
    int base = chunk->size();
    chunk->insert(chunk->end(), s.begin(), s.end());
    return std::string_view(chunk->data() + base, s.size());
}

Datadog::Sample::Sample(SampleType _type_mask, unsigned int _max_nframes)
  : max_nframes{ _max_nframes }
  , type_mask{ _type_mask }
{
    // Initialize values
    values.resize(profile_state.get_sample_type_length());
    std::fill(values.begin(), values.end(), 0);

    // Initialize other state
    locations.reserve(max_nframes + 1); // +1 for a "truncated frames" virtual frame
}

void
Datadog::Sample::profile_clear_state()
{
    profile_state.cycle_buffers();
}

void
Datadog::Sample::push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{
    static const ddog_prof_Mapping null_mapping = { 0, 0, 0, to_slice(""), { 0 }, to_slice(""), { 0 } };
    name = string_storage.insert(name);
    filename = string_storage.insert(filename);

    const ddog_prof_Location loc = {
        .mapping = null_mapping, // No support for mappings in Python
        .function = {
          .name = to_slice(name),
          .name_id = { 0 },
          .system_name = {}, // No support for system_name in Python
          .system_name_id = { 0 },
          .filename = to_slice(filename),
          .filename_id = { 0 },
          .start_line = 0, // We don't know the start_line for the function
        },
        .address = address,
        .line = line,
    };

    locations.emplace_back(loc);
}

void
Datadog::Sample::push_frame(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{

    if (locations.size() <= max_nframes) {
        push_frame_impl(name, filename, address, line);
    } else {
        ++dropped_frames;
    }
}

bool
Datadog::Sample::push_label(const ExportLabelKey key, std::string_view val)
{
    // Get the sv for the key
    const std::string_view key_sv = to_string(key);

    // If either the val or the key are empty, we don't add the label, but
    // we don't return error
    // TODO is this what we want?
    if (val.empty() || key_sv.empty()) {
        return true;
    }

    // Otherwise, persist the val string and add the label
    val = string_storage.insert(val);
    auto& label = labels.emplace_back();
    label.key = to_slice(key_sv);
    label.str = to_slice(val);
    return true;
}

bool
Datadog::Sample::push_label(const ExportLabelKey key, int64_t val)
{
    // Get the sv for the key.  If there is no key, then there
    // is no label.  Right now this is OK.
    // TODO make this not OK
    const std::string_view key_sv = to_string(key);
    if (key_sv.empty()) {
        return true;
    }

    auto& label = labels.emplace_back();
    label.key = to_slice(key_sv);
    label.str = to_slice("");
    label.num = val;
    label.num_unit = to_slice("");
    return true;
}

void
Datadog::Sample::clear_buffers()
{
    std::fill(values.begin(), values.end(), 0);
    labels.clear();
    locations.clear();
    dropped_frames = 0;
    string_storage.reset();
}

bool
Datadog::Sample::flush_sample(bool reverse_locations)
{
    if (dropped_frames > 0) {
        const std::string name =
          "<" + std::to_string(dropped_frames) + " frame" + (1 == dropped_frames ? "" : "s") + " omitted>";
        Sample::push_frame_impl(name, "", 0, 0);
    }

    if (reverse_locations) {
        std::reverse(locations.begin(), locations.end());
    }

    const ddog_prof_Sample sample = {
        .locations = { locations.data(), locations.size() },
        .values = { values.data(), values.size() },
        .labels = { labels.data(), labels.size() },
    };

    const bool ret = profile_state.collect(sample, endtime_ns);
    clear_buffers();
    return ret;
}

bool
Datadog::Sample::push_cputime(int64_t cputime, int64_t count)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    // NB all push-type operations return bool for semantic uniformity,
    // even if they can't error.  This should promote generic code.
    if (0U != (type_mask & SampleType::CPU)) {
        values[profile_state.val().cpu_time] += cputime * count;
        values[profile_state.val().cpu_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push cpu" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_walltime(int64_t walltime, int64_t count)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::Wall)) {
        values[profile_state.val().wall_time] += walltime * count;
        values[profile_state.val().wall_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push wall" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_exceptioninfo(std::string_view exception_type, int64_t count)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::Exception)) {
        push_label(ExportLabelKey::exception_type, exception_type);
        values[profile_state.val().exception_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push except" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_acquire(int64_t acquire_time, int64_t count) // NOLINT (bugprone-easily-swappable-parameters)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::LockAcquire)) {
        values[profile_state.val().lock_acquire_time] += acquire_time;
        values[profile_state.val().lock_acquire_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push acquire" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_release(int64_t lock_time, int64_t count) // NOLINT (bugprone-easily-swappable-parameters)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::LockRelease)) {
        values[profile_state.val().lock_release_time] += lock_time;
        values[profile_state.val().lock_release_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push release" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_alloc(int64_t size, int64_t count) // NOLINT (bugprone-easily-swappable-parameters)
{
    static bool already_warned_params = false;
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety

    if (size < 0 || count < 0) {
        if (!already_warned_params) {
            already_warned_params = true;
            std::cerr << "bad push alloc (params)" << std::endl;
        }
        return false;
    }

    if (0U != (type_mask & SampleType::Allocation)) {
        values[profile_state.val().alloc_space] += size;
        values[profile_state.val().alloc_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push alloc" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_heap(int64_t size)
{
    static bool already_warned_params = false;
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety

    if (size < 0) {
        if (!already_warned_params) {
            already_warned_params = true;
            std::cerr << "bad push heap (params)" << std::endl;
        }
        return false;
    }

    if (0U != (type_mask & SampleType::Heap)) {
        values[profile_state.val().heap_space] += size;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push heap" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_gpu_gputime(int64_t time, int64_t count)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::GPUTime)) {
        values[profile_state.val().gpu_time] += time * count;
        values[profile_state.val().gpu_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push gpu" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_gpu_memory(int64_t size, int64_t count)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::GPUMemory)) {
        values[profile_state.val().gpu_alloc_space] += size * count;
        values[profile_state.val().gpu_alloc_count] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push gpu memory" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_gpu_flops(int64_t size, int64_t count)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (0U != (type_mask & SampleType::GPUFlops)) {
        values[profile_state.val().gpu_flops] += size * count;
        values[profile_state.val().gpu_flops_samples] += count;
        return true;
    }
    if (!already_warned) {
        already_warned = true;
        std::cerr << "bad push gpu flops" << std::endl;
    }
    return false;
}

bool
Datadog::Sample::push_lock_name(std::string_view lock_name)
{
    push_label(ExportLabelKey::lock_name, lock_name);
    return true;
}

bool
Datadog::Sample::push_threadinfo(int64_t thread_id, int64_t thread_native_id, std::string_view thread_name)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    std::string temp_string;
    if (thread_name.empty()) {
        temp_string = std::to_string(thread_id);
        thread_name = temp_string;
    }
    if (!push_label(ExportLabelKey::thread_id, thread_id) ||
        !push_label(ExportLabelKey::thread_native_id, thread_native_id) ||
        !push_label(ExportLabelKey::thread_name, thread_name)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_task_id(int64_t task_id)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!push_label(ExportLabelKey::task_id, task_id)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}
bool
Datadog::Sample::push_task_name(std::string_view task_name)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!push_label(ExportLabelKey::task_name, task_name)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_span_id(uint64_t span_id)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    // The pprof container expects a signed 64-bit integer for numeric labels, whereas
    // the emitted ID is unsigned (full 64-bit range).  We type-pun to int64_t here.
    const int64_t recoded_id =
      reinterpret_cast<int64_t&>(span_id); // NOLINT (cppcoreguidelines-pro-type-reinterpret-cast)
    if (!push_label(ExportLabelKey::span_id, recoded_id)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_local_root_span_id(uint64_t local_root_span_id)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    const int64_t recoded_id =
      reinterpret_cast<int64_t&>(local_root_span_id); // NOLINT (cppcoreguidelines-pro-type-reinterpret-cast)
    if (!push_label(ExportLabelKey::local_root_span_id, recoded_id)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_trace_type(std::string_view trace_type)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!push_label(ExportLabelKey::trace_type, trace_type)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_class_name(std::string_view class_name)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!push_label(ExportLabelKey::class_name, class_name)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_gpu_device_name(std::string_view device_name)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!push_label(ExportLabelKey::gpu_device_name, device_name)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "bad push" << std::endl;
        }
        return false;
    }
    return true;
}

bool
Datadog::Sample::push_absolute_ns(int64_t _timestamp_ns)
{
    // If timeline is not enabled, then this is a no-op
    if (is_timeline_enabled()) {
        endtime_ns = _timestamp_ns;
    }

    return true;
}

bool
Datadog::Sample::push_monotonic_ns(int64_t _monotonic_ns)
{
    // Monotonic times have their epoch at the system start, so they need an
    // adjustment to the standard epoch
    // Just set a static for now and use a lambda to compute the offset once
    const static auto offset = []() {
        // Get the current epoch time
        using namespace std::chrono;
        auto epoch_ns = duration_cast<nanoseconds>(system_clock::now().time_since_epoch()).count();

#ifdef _WIN32
        // https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps
        LARGE_INTEGER frequency, counter;
        QueryPerformanceFrequency(&frequency); // Frequency of the performance counter
        QueryPerformanceCounter(&counter);     // Current value of the performance counter

        // Convert to nanoseconds
        auto monotonic_ns = (counter.QuadPart * 1'000'000'000LL) / frequency.QuadPart;
#else
        // Get the current monotonic time.  Use clock_gettime directly because the standard underspecifies
        // which clock is actually used in std::chrono
        timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        auto monotonic_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;
#endif

        // Compute the difference.  We're after 1970, so epoch_ns will be larger
        return epoch_ns - monotonic_ns;
    }();

    // If timeline is not enabled, then this is a no-op
    if (is_timeline_enabled()) {
        endtime_ns = _monotonic_ns + offset;
    }

    return true;
}

void
Datadog::Sample::set_timeline(bool enabled)
{
    timeline_enabled = enabled;
}

bool
Datadog::Sample::is_timeline_enabled() const
{
    return timeline_enabled;
}

ddog_prof_Profile&
Datadog::Sample::profile_borrow()
{
    return profile_state.profile_borrow();
}

void
Datadog::Sample::profile_release()
{
    profile_state.profile_release();
}

void
Datadog::Sample::postfork_child()
{
    profile_state.postfork_child();
}
