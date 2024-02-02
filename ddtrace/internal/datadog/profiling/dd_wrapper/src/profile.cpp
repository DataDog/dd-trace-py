// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#include "profile.hpp"
#include "global_cache.hpp"

#include <thread>

using namespace Datadog;

Profile::Profile(ProfileType type, unsigned int _max_nframes)
{

    shared_state.one_time_init(type, _max_nframes);

    // Initialize values
    values.resize(shared_state.get_sample_type_length());
    std::fill(values.begin(), values.end(), 0);
    max_nframes = shared_state.get_max_nframes();
    type_mask = shared_state.get_type_mask();

    // Initialize other state
    profile_seq = shared_state.get_profile_seq();
    locations.reserve(max_nframes + 1); // +1 for a "truncated frames" virtual frame
    cur_label = 0;
}

ddog_prof_Profile&
Profile::get_ddog_profile()
{
    return shared_state.get_current_profile();
}

Profile&
Profile::start_sample()
{
    shared_state.entrypoint_check();

    // This initializes the sampling state of the profile.  In particular, it
    // acquires a collection object from global storage on behalf of the user,
    // creating it if it isn't already present.
    auto thread_id = std::this_thread::get_id();
    return ProfileGlobalStorage::get(thread_id);
}

void
Profile::push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{
    static const ddog_prof_Mapping null_mapping = { 0, 0, 0, to_slice(""), to_slice("") };
    name = shared_state.insert_or_get(name);
    filename = shared_state.insert_or_get(filename);

    ddog_prof_Location loc = {
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
Profile::push_frame(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{

    if (locations.size() <= max_nframes)
        push_frame_impl(name, filename, address, line);
}

bool
Profile::push_label(const ExportLabelKey key, std::string_view val)
{
    if (cur_label >= labels.size()) {
        return false;
    }

    // Get the sv for the key
    std::string_view key_sv = str_from_key(key);

    // If either the val or the key are empty, we don't add the label, but
    // we don't return error
    // TODO is this what we want?
    if (val.empty() || key_sv.empty())
        return true;

    // Otherwise, persist the val string and add the label
    val = shared_state.insert_or_get(val);
    labels[cur_label].key = to_slice(key_sv);
    labels[cur_label].str = to_slice(val);
    cur_label++;
    return true;
}

bool
Profile::push_label(const ExportLabelKey key, int64_t val)
{
    if (cur_label >= labels.size()) {
        std::cout << "Bad push_label (num)" << std::endl;
        return false;
    }

    // Get the sv for the key.  If there is no key, then there
    // is no label.  Right now this is OK.
    // TODO make this not OK
    std::string_view key_sv = str_from_key(key);
    if (key_sv.empty())
        return true;

    labels[cur_label].key = to_slice(key_sv);
    labels[cur_label].str = to_slice("");
    labels[cur_label].num = val;
    labels[cur_label].num_unit = to_slice("");
    cur_label++;
    return true;
}

void
Profile::clear_buffers()
{
    std::fill(values.begin(), values.end(), 0);
    std::fill(std::begin(labels), std::end(labels), ddog_prof_Label{});
    locations.clear();
    cur_label = 0;
}

bool
Profile::flush_sample()
{
    // Profiles are local to threads, meaning they are local to processes.  We can only arrive here if
    // we're flushing in the same process.
    if (locations.size() > max_nframes) {
        auto dropped_frames = locations.size() - max_nframes;
        std::string name =
          "<" + std::to_string(dropped_frames) + " frame" + (1 == dropped_frames ? "" : "s") + " omitted>";
        Profile::push_frame_impl(name, "", 0, 0);
    }

    ddog_prof_Sample sample = {
        .locations = { locations.data(), locations.size() },
        .values = { values.data(), values.size() },
        .labels = { labels.data(), cur_label },
    };

    bool ret = shared_state.flush_sample(sample);
    clear_buffers();
    return ret;
}

bool
Profile::push_cputime(int64_t cputime, int64_t count)
{
    // NB all push-type operations return bool for semantic uniformity,
    // even if they can't error.  This should promote generic code.
    if (type_mask & ProfileType::CPU) {
        values[shared_state.val().cpu_time] += cputime * count;
        values[shared_state.val().cpu_count] += count;
        return true;
    }
    std::cout << "bad push cpu" << std::endl;
    return false;
}

bool
Profile::push_walltime(int64_t walltime, int64_t count)
{
    if (type_mask & ProfileType::Wall) {
        values[shared_state.val().wall_time] += walltime * count;
        values[shared_state.val().wall_count] += count;
        return true;
    }
    std::cout << "bad push wall" << std::endl;
    return false;
}

bool
Profile::push_exceptioninfo(std::string_view exception_type, int64_t count)
{
    if (type_mask & ProfileType::Exception) {
        push_label(ExportLabelKey::exception_type, exception_type);
        values[shared_state.val().exception_count] += count;
        return true;
    }
    std::cout << "bad push except" << std::endl;
    return false;
}

bool
Profile::push_acquire(int64_t acquire_time, int64_t count)
{
    if (type_mask & ProfileType::LockAcquire) {
        values[shared_state.val().lock_acquire_time] += acquire_time;
        values[shared_state.val().lock_acquire_count] += count;
        return true;
    }
    std::cout << "bad push acquire" << std::endl;
    return false;
}

bool
Profile::push_release(int64_t release_time, int64_t count)
{
    if (type_mask & ProfileType::LockRelease) {
        values[shared_state.val().lock_release_time] += release_time;
        values[shared_state.val().lock_release_count] += count;
        return true;
    }
    std::cout << "bad push release" << std::endl;
    return false;
}

bool
Profile::push_alloc(uint64_t size, uint64_t count)
{
    if (type_mask & ProfileType::Allocation) {
        values[shared_state.val().alloc_space] += size;
        values[shared_state.val().alloc_count] += count;
        return true;
    }
    std::cout << "bad push alloc" << std::endl;
    return false;
}

bool
Profile::push_heap(uint64_t size)
{
    if (type_mask & ProfileType::Heap) {
        values[shared_state.val().heap_space] += size;
        return true;
    }
    std::cout << "bad push heap" << std::endl;
    return false;
}

bool
Profile::push_lock_name(std::string_view lock_name)
{
    push_label(ExportLabelKey::lock_name, lock_name);
    return true;
}

bool
Profile::push_threadinfo(int64_t thread_id, int64_t thread_native_id, std::string_view thread_name)
{
    if (thread_name.empty()) {
        thread_name = std::to_string(thread_id);
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
Profile::push_task_id(int64_t task_id)
{
    if (!push_label(ExportLabelKey::task_id, task_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}
bool
Profile::push_task_name(std::string_view task_name)
{
    if (!push_label(ExportLabelKey::task_name, task_name)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Profile::push_span_id(uint64_t span_id)
{
    int64_t recoded_id = reinterpret_cast<int64_t&>(span_id);
    if (!push_label(ExportLabelKey::span_id, recoded_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Profile::push_local_root_span_id(uint64_t local_root_span_id)
{
    int64_t recoded_id = reinterpret_cast<int64_t&>(local_root_span_id);
    if (!push_label(ExportLabelKey::local_root_span_id, recoded_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Profile::push_trace_type(std::string_view trace_type)
{
    if (!push_label(ExportLabelKey::trace_type, trace_type)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Profile::push_trace_resource_container(std::string_view trace_resource_container)
{
    if (!push_label(ExportLabelKey::trace_resource_container, trace_resource_container)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Profile::push_class_name(std::string_view class_name)
{
    if (!push_label(ExportLabelKey::class_name, class_name)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}
