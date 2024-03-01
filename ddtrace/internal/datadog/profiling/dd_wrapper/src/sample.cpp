#include "sample.hpp"

#include <thread>

using namespace Datadog;

Sample::Sample(SampleType _type_mask, unsigned int _max_nframes)
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
Sample::profile_clear_state()
{
    profile_state.cycle_buffers();
}

void
Sample::push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{
    static const ddog_prof_Mapping null_mapping = { 0, 0, 0, to_slice(""), to_slice("") };
    name = profile_state.insert_or_get(name);
    filename = profile_state.insert_or_get(filename);

    const ddog_prof_Location loc = {
        .mapping = null_mapping, // No support for mappings in Python
        .function = {
          .name = to_slice(name),
          .system_name = {}, // No support for system_name in Python
          .filename = to_slice(filename),
          .start_line = 0, // We don't know the start_line for the function
        },
        .address = address,
        .line = line,
    };

    locations.emplace_back(loc);
}

void
Sample::push_frame(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{

    if (locations.size() <= max_nframes) {
        push_frame_impl(name, filename, address, line);
    } else {
        ++dropped_frames;
    }
}

bool
Sample::push_label(const ExportLabelKey key, std::string_view val)
{
    if (cur_label >= labels.size()) {
        return false;
    }

    // Get the sv for the key
    const std::string_view key_sv = to_string(key);

    // If either the val or the key are empty, we don't add the label, but
    // we don't return error
    // TODO is this what we want?
    if (val.empty() || key_sv.empty()) {
        return true;
    }

    // Otherwise, persist the val string and add the label
    val = profile_state.insert_or_get(val);
    labels[cur_label].key = to_slice(key_sv);
    labels[cur_label].str = to_slice(val);
    cur_label++;
    return true;
}

bool
Sample::push_label(const ExportLabelKey key, int64_t val)
{
    if (cur_label >= labels.size()) {
        std::cout << "Bad push_label (num)" << std::endl;
        return false;
    }

    // Get the sv for the key.  If there is no key, then there
    // is no label.  Right now this is OK.
    // TODO make this not OK
    const std::string_view key_sv = to_string(key);
    if (key_sv.empty()) {
        return true;
    }

    labels[cur_label].key = to_slice(key_sv);
    labels[cur_label].str = to_slice("");
    labels[cur_label].num = val;
    labels[cur_label].num_unit = to_slice("");
    cur_label++;
    return true;
}

void
Sample::clear_buffers()
{
    std::fill(values.begin(), values.end(), 0);
    std::fill(std::begin(labels), std::end(labels), ddog_prof_Label{});
    locations.clear();
    cur_label = 0;
    dropped_frames = 0;
}

bool
Sample::flush_sample()
{
    if (dropped_frames > 0) {
        const std::string name =
          "<" + std::to_string(dropped_frames) + " frame" + (1 == dropped_frames ? "" : "s") + " omitted>";
        Sample::push_frame_impl(name, "", 0, 0);
    }

    const ddog_prof_Sample sample = {
        .locations = { locations.data(), locations.size() },
        .values = { values.data(), values.size() },
        .labels = { labels.data(), cur_label },
    };

    const bool ret = profile_state.collect(sample);
    clear_buffers();
    return ret;
}

bool
Sample::push_cputime(int64_t cputime, int64_t count)
{
    // NB all push-type operations return bool for semantic uniformity,
    // even if they can't error.  This should promote generic code.
    if (type_mask & SampleType::CPU) {
        values[profile_state.val().cpu_time] += cputime * count;
        values[profile_state.val().cpu_count] += count;
        return true;
    }
    std::cout << "bad push cpu" << std::endl;
    return false;
}

bool
Sample::push_walltime(int64_t walltime, int64_t count)
{
    if (type_mask & SampleType::Wall) {
        values[profile_state.val().wall_time] += walltime * count;
        values[profile_state.val().wall_count] += count;
        return true;
    }
    std::cout << "bad push wall" << std::endl;
    return false;
}

bool
Sample::push_exceptioninfo(std::string_view exception_type, int64_t count)
{
    if (type_mask & SampleType::Exception) {
        push_label(ExportLabelKey::exception_type, exception_type);
        values[profile_state.val().exception_count] += count;
        return true;
    }
    std::cout << "bad push except" << std::endl;
    return false;
}

bool
Sample::push_acquire(int64_t acquire_time, int64_t count)
{
    if (type_mask & SampleType::LockAcquire) {
        values[profile_state.val().lock_acquire_time] += acquire_time;
        values[profile_state.val().lock_acquire_count] += count;
        return true;
    }
    std::cout << "bad push acquire" << std::endl;
    return false;
}

bool
Sample::push_release(int64_t lock_time, int64_t count)
{
    if (type_mask & SampleType::LockRelease) {
        values[profile_state.val().lock_release_time] += lock_time;
        values[profile_state.val().lock_release_count] += count;
        return true;
    }
    std::cout << "bad push release" << std::endl;
    return false;
}

bool
Sample::push_alloc(uint64_t size, uint64_t count)
{
    if (type_mask & SampleType::Allocation) {
        values[profile_state.val().alloc_space] += size;
        values[profile_state.val().alloc_count] += count;
        return true;
    }
    std::cout << "bad push alloc" << std::endl;
    return false;
}

bool
Sample::push_heap(uint64_t size)
{
    if (type_mask & SampleType::Heap) {
        values[profile_state.val().heap_space] += size;
        return true;
    }
    std::cout << "bad push heap" << std::endl;
    return false;
}

bool
Sample::push_lock_name(std::string_view lock_name)
{
    push_label(ExportLabelKey::lock_name, lock_name);
    return true;
}

bool
Sample::push_threadinfo(int64_t thread_id, int64_t thread_native_id, std::string_view thread_name)
{
    std::string temp_string;
    if (thread_name.empty()) {
        temp_string = std::to_string(thread_id);
        thread_name = temp_string;
    }
    if (!push_label(ExportLabelKey::thread_id, thread_id) ||
        !push_label(ExportLabelKey::thread_native_id, thread_native_id) ||
        !push_label(ExportLabelKey::thread_name, thread_name)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Sample::push_task_id(int64_t task_id)
{
    if (!push_label(ExportLabelKey::task_id, task_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}
bool
Sample::push_task_name(std::string_view task_name)
{
    if (!push_label(ExportLabelKey::task_name, task_name)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Sample::push_span_id(uint64_t span_id)
{
    const int64_t recoded_id = reinterpret_cast<int64_t&>(span_id);
    if (!push_label(ExportLabelKey::span_id, recoded_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Sample::push_local_root_span_id(uint64_t local_root_span_id)
{
    const int64_t recoded_id = reinterpret_cast<int64_t&>(local_root_span_id);
    if (!push_label(ExportLabelKey::local_root_span_id, recoded_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Sample::push_trace_type(std::string_view trace_type)
{
    if (!push_label(ExportLabelKey::trace_type, trace_type)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Sample::push_trace_resource_container(std::string_view trace_resource_container)
{
    if (!push_label(ExportLabelKey::trace_resource_container, trace_resource_container)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Sample::push_class_name(std::string_view class_name)
{
    if (!push_label(ExportLabelKey::class_name, class_name)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

ddog_prof_Profile&
Sample::profile_borrow()
{
    return profile_state.profile_borrow();
}

void
Sample::profile_release()
{
    profile_state.profile_release();
}

void
Sample::postfork_child()
{
    profile_state.postfork_child();
}
