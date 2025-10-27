#include "sample.hpp"

#include "libdatadog_helpers.hpp"

#include <algorithm>
#include <chrono>
#include <datadog/profiling.h>
#include <map>
#include <string>
#include <string_view>

static ddog_prof_ManagedStringStorage*
managed_string_storage()
{
    static ddog_prof_ManagedStringStorage string_storage_value;
    static bool initialized = false;
    if (!initialized) {
        std::cerr << "creating string storage" << std::endl;
        auto result = ddog_prof_ManagedStringStorage_new();
        if (result.tag == DDOG_PROF_MANAGED_STRING_STORAGE_NEW_RESULT_ERR) {
            auto err = result.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
            auto errmsg = Datadog::err_to_msg(&err, "Error creating managed string storage");
            std::cerr << "couldn't create string storage: " << errmsg << std::endl;
            ddog_Error_drop(&err);
            return nullptr;
        }

        std::cerr << "created string storage" << std::endl;
        string_storage_value = result.ok;
        initialized = true;
    }

    return &string_storage_value;
}

// Thread-local cache to avoid repeated FFI calls for string interning
// Uses std::map with transparent comparison for zero-allocation string_view lookups
struct StringIdCache
{
    static constexpr size_t MAX_CACHE_SIZE = 10000;
    // std::less<> provides transparent comparison allowing string_view lookups without allocation
    std::map<std::string, ddog_prof_ManagedStringId, std::less<>> cache;

    ddog_prof_ManagedStringId get_or_intern(std::string_view str, std::string& errmsg)
    {
        // Fast path: transparent lookup with string_view (no temporary string allocation)
        auto it = cache.find(str);
        if (it != cache.end()) {
            return it->second;
        }

        // Slow path: intern the string via FFI
        auto maybe_id = ddog_prof_ManagedStringStorage_intern(*managed_string_storage(), Datadog::to_slice(str));
        if (maybe_id.tag == DDOG_PROF_MANAGED_STRING_STORAGE_INTERN_RESULT_ERR) {
            auto err = maybe_id.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
            errmsg = Datadog::err_to_msg(&err, "Error interning string");
            ddog_Error_drop(&err);
            return { 0 }; // Return invalid ID on error
        }

        auto id = maybe_id.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)

        // Add to cache if not too large
        if (cache.size() < MAX_CACHE_SIZE) {
            cache.emplace(std::string(str), id);
        }

        return id;
    }
};

static StringIdCache&
get_string_id_cache()
{
    thread_local StringIdCache cache;
    return cache;
}

// Public function to intern a string and return the ID
// This is used by the optimized Key-based path where interning happens in stack_v2
ddog_prof_ManagedStringId
intern_string_to_id(std::string_view str)
{
    auto maybe_id = ddog_prof_ManagedStringStorage_intern(*managed_string_storage(), Datadog::to_slice(str));
    if (maybe_id.tag == DDOG_PROF_MANAGED_STRING_STORAGE_INTERN_RESULT_ERR) {
        auto err = maybe_id.err; // NOLINT (cppcoreguidelines-pro-type-union-access)
        std::cerr << "Error interning string: " << Datadog::err_to_msg(&err, "intern") << std::endl;
        ddog_Error_drop(&err);
        return { 0 };
    }
    return maybe_id.ok; // NOLINT (cppcoreguidelines-pro-type-union-access)
}

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
Datadog::Sample::push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{
    static const ddog_prof_Mapping null_mapping = { 0, 0, 0, to_slice(""), { 0 }, to_slice(""), { 0 } };

    auto& cache = get_string_id_cache();

    auto name_id = cache.get_or_intern(name, errmsg);
    if (name_id.value == 0 && !name.empty()) {
        std::cerr << "error interning name: " << errmsg << std::endl;
        return;
    }

    auto filename_id = cache.get_or_intern(filename, errmsg);
    if (filename_id.value == 0 && !filename.empty()) {
        std::cerr << "error interning filename: " << errmsg << std::endl;
        return;
    }

    const ddog_prof_Location loc = {
        .mapping = null_mapping, // No support for mappings in Python
        .function = {
          .name = {},
          .name_id = name_id,
          .system_name = {}, // No support for system_name in Python
          .system_name_id = {}, // No support for system_name in Python
          .filename = {},
          .filename_id = filename_id,
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

void
Datadog::Sample::push_frame_impl_ids(ddog_prof_ManagedStringId name_id,
                                     ddog_prof_ManagedStringId filename_id,
                                     uint64_t address,
                                     int64_t line)
{
    static const ddog_prof_Mapping null_mapping = { 0, 0, 0, to_slice(""), { 0 }, to_slice(""), { 0 } };

    const ddog_prof_Location loc = {
        .mapping = null_mapping, // No support for mappings in Python
        .function = {
          .name = {},
          .name_id = name_id,
          .system_name = {}, // No support for system_name in Python
          .system_name_id = {}, // No support for system_name in Python
          .filename = {},
          .filename_id = filename_id,
        },
        .address = address,
        .line = line,
    };

    locations.emplace_back(loc);
}

void
Datadog::Sample::push_frame_ids(ddog_prof_ManagedStringId name_id,
                                ddog_prof_ManagedStringId filename_id,
                                uint64_t address,
                                int64_t line)
{
    if (locations.size() <= max_nframes) {
        push_frame_impl_ids(name_id, filename_id, address, line);
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

    auto& cache = get_string_id_cache();

    auto val_id = cache.get_or_intern(val, errmsg);
    if (val_id.value == 0) {
        std::cerr << "error interning value: " << errmsg << std::endl;
        return false;
    }

    auto key_id = cache.get_or_intern(key_sv, errmsg);
    if (key_id.value == 0) {
        std::cerr << "error interning key: " << errmsg << std::endl;
        return false;
    }

    auto& label = labels.emplace_back();
    label.key_id = key_id;
    label.str_id = val_id;
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
Datadog::Sample::push_acquire(int64_t acquire_time,
                              int64_t count) // NOLINT (bugprone-easily-swappable-parameters)
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
Datadog::Sample::push_release(int64_t lock_time,
                              int64_t count) // NOLINT (bugprone-easily-swappable-parameters)
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
Datadog::Sample::push_task_name_id(uint32_t task_name_id)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!push_label(ExportLabelKey::task_name, task_name_id)) {
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

        // Get the current monotonic time.  Use clock_gettime directly because the standard underspecifies
        // which clock is actually used in std::chrono
        timespec ts;
        clock_gettime(CLOCK_MONOTONIC, &ts);
        auto monotonic_ns = static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;

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
