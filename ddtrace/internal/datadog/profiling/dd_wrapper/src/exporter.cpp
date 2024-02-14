// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.
#include "exporter.hpp"
#include <initializer_list>
#include <iostream>
#include <stdexcept>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

using namespace Datadog;

inline ddog_CharSlice
to_slice(std::string_view str)
{
    return { .ptr = str.data(), .len = str.size() };
}

UploaderBuilder&
UploaderBuilder::set_env(std::string_view _env)
{
    if (!_env.empty())
        env = _env;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_service(std::string_view _service)
{
    if (!_service.empty())
        service = _service;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_version(std::string_view _version)
{
    if (!_version.empty())
        version = _version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_runtime(std::string_view _runtime)
{
    if (!_runtime.empty())
        runtime = _runtime;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_runtime_version(std::string_view _runtime_version)
{
    if (!_runtime_version.empty())
        runtime_version = _runtime_version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_profiler_version(std::string_view _profiler_version)
{
    if (!_profiler_version.empty())
        profiler_version = _profiler_version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_url(std::string_view _url)
{
    if (!_url.empty())
        url = _url;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_tag(std::string_view key, std::string_view val)
{
    if (!key.empty() && !val.empty())
        user_tags[key] = val;
    return *this;
}

void
DdogProfExporterDeleter::operator()(ddog_prof_Exporter* ptr) const
{
    if (ptr)
        ddog_prof_Exporter_drop(ptr);
}

#define X_STR(a, b) b,
bool
add_tag(ddog_Vec_Tag& tags, const ExportTagKey key, std::string_view val, std::string& errmsg)
{
    // NB the storage of `val` needs to be guaranteed until the tags are flushed
    constexpr std::array<std::string_view, static_cast<size_t>(ExportTagKey::_Length)> keys = { EXPORTER_TAGS(X_STR) };

    // If the value is empty, return an error.
    if (val.empty())
        return false;
    std::string_view key_sv = keys[static_cast<size_t>(key)];

    // Add
    ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key_sv), to_slice(val));
    if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
        std::string ddog_err(ddog_Error_message(&res.err).ptr);
        errmsg = "tags[" + std::string(key_sv) + "]='" + std::string(val) + " err: '" + ddog_err + "'";
        ddog_Error_drop(&res.err);
        return false;
    }
    return true;
}

bool
add_tag_unsafe(ddog_Vec_Tag& tags, std::string_view key, std::string_view val, std::string& errmsg)
{
    if (key.empty() || val.empty())
        return false;

    ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key), to_slice(val));
    if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
        std::string ddog_err(ddog_Error_message(&res.err).ptr);
        errmsg = "tags[" + std::string(key) + "]='" + std::string(val) + " err: '" + ddog_err + "'";
        ddog_Error_drop(&res.err);
        std::cout << errmsg << std::endl;
        return false;
    }
    return true;
}

Uploader*
UploaderBuilder::build_ptr()
{
    // Setup the ddog_Exporter
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();

    // These three tags are guaranteed by the backend; they can be omitted if needed
    if (!env.empty())
        add_tag(tags, ExportTagKey::env, env, errmsg);
    if (!service.empty())
        add_tag(tags, ExportTagKey::service, service, errmsg);
    if (!version.empty())
        add_tag(tags, ExportTagKey::version, version, errmsg);

    // Assume that these tags are all populated + correct
    if (!add_tag(tags, ExportTagKey::language, language, errmsg) ||
        !add_tag(tags, ExportTagKey::runtime, runtime, errmsg) ||
        !add_tag(tags, ExportTagKey::runtime_version, runtime_version, errmsg) ||
        !add_tag(tags, ExportTagKey::profiler_version, profiler_version, errmsg)) {
        return nullptr;
    }

    // Add the unsafe tags, if any
    for (const auto& kv : user_tags)
        if (!add_tag_unsafe(tags, kv.first, kv.second, errmsg))
            return nullptr;

    ddog_prof_Exporter_NewResult new_exporter = ddog_prof_Exporter_new(
      to_slice("dd-trace-py"), to_slice(profiler_version), to_slice(family), &tags, ddog_Endpoint_agent(to_slice(url)));
    ddog_Vec_Tag_drop(tags);

    ddog_prof_Exporter* ddog_exporter = nullptr;
    if (new_exporter.tag == DDOG_PROF_EXPORTER_NEW_RESULT_OK) {
        ddog_exporter = new_exporter.ok;
    } else {
        std::string ddog_err(ddog_Error_message(&new_exporter.err).ptr);
        errmsg = "Could not initialize exporter, err: " + ddog_err;
        ddog_Error_drop(&new_exporter.err);
        return nullptr;
    }

    return new Uploader(url, ddog_exporter);
}

Uploader::Uploader(std::string_view _url, ddog_prof_Exporter* _ddog_exporter)
  : ddog_exporter{ _ddog_exporter }
  , url{ _url }
{}

bool
Uploader::set_runtime_id(std::string_view id)
{
    runtime_id = std::string(id);
    return true;
}

bool
Uploader::upload(const Profile* profile)
{
    ddog_prof_Profile* ddog_profile = const_cast<ddog_prof_Profile*>(&profile->ddog_profile);
    ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(ddog_profile, nullptr, nullptr, nullptr);
    if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
        std::string ddog_err(ddog_Error_message(&result.err).ptr);
        errmsg = "Error serializing pprof, err:" + ddog_err;
        ddog_Error_drop(&result.err);
        std::cout << errmsg << std::endl;
        return false;
    }

    ddog_prof_EncodedProfile* encoded = &result.ok;

    ddog_Timespec start = encoded->start;
    ddog_Timespec end = encoded->end;

    // Attach file
    ddog_prof_Exporter_File file[] = {
        {
          .name = to_slice("auto.pprof"),
          .file = ddog_Vec_U8_as_slice(&encoded->buffer),
        },
    };

    // If we have any custom tags, set them now
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    add_tag(tags, ExportTagKey::profile_seq, std::to_string(profile_seq++), errmsg);
    add_tag(tags, ExportTagKey::runtime_id, runtime_id, errmsg);

    // Build the request object
    const uint64_t max_timeout_ms = 5000; // 5s is a common timeout parameter for Datadog profilers
    auto build_res = ddog_prof_Exporter_Request_build(ddog_exporter.get(),
                                                      start,
                                                      end,
                                                      ddog_prof_Exporter_Slice_File_empty(),
                                                      { .ptr = file, .len = 1 },
                                                      &tags,
                                                      nullptr,
                                                      nullptr,
                                                      max_timeout_ms);

    if (build_res.tag == DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) {
        std::string ddog_err(ddog_Error_message(&build_res.err).ptr);
        errmsg = "Error building request, err:" + ddog_err;
        ddog_Error_drop(&build_res.err);
        ddog_prof_EncodedProfile_drop(encoded);
        ddog_Vec_Tag_drop(tags);
        std::cout << errmsg << std::endl;
        return false;
    }

    // Build and check the response object
    ddog_prof_Exporter_Request* req = build_res.ok;
    ddog_prof_Exporter_SendResult res = ddog_prof_Exporter_send(ddog_exporter.get(), &req, nullptr);
    if (res.tag == DDOG_PROF_EXPORTER_SEND_RESULT_ERR) {
        std::string ddog_err(ddog_Error_message(&res.err).ptr);
        errmsg = "Failed to upload (url:'" + url + "'), err: " + ddog_err;
        ddog_Error_drop(&res.err);
        ddog_prof_EncodedProfile_drop(encoded);
        ddog_Vec_Tag_drop(tags);
        std::cout << errmsg << std::endl;
        return false;
    }

    // Cleanup
    ddog_prof_Exporter_Request_drop(&req);
    ddog_prof_EncodedProfile_drop(encoded);
    ddog_Vec_Tag_drop(tags);

    return true;
}

ProfileBuilder&
ProfileBuilder::add_type(Profile::ProfileType type)
{
    unsigned int mask_as_int = (type_mask | type) & Profile::ProfileType::All;
    type_mask = static_cast<Profile::ProfileType>(mask_as_int);
    return *this;
}

ProfileBuilder&
ProfileBuilder::add_type(unsigned int type)
{
    return add_type(static_cast<Profile::ProfileType>(type));
}

ProfileBuilder&
ProfileBuilder::set_max_nframes(unsigned int _max_nframes)
{
    if (_max_nframes > 0)
        max_nframes = _max_nframes;
    return *this;
}

Profile*
ProfileBuilder::build_ptr()
{
    return new Profile(type_mask, max_nframes);
}

Profile::Profile(ProfileType type, unsigned int _max_nframes)
  : type_mask{ type & ProfileType::All }
  , max_nframes{ _max_nframes }
{
    // Push an element to the end of the vector, returning the position of
    // insertion
    std::vector<ddog_prof_ValueType> samplers{};
    auto get_value_idx = [&samplers](std::string_view value, std::string_view unit) {
        size_t idx = samplers.size();
        samplers.push_back({ to_slice(value), to_slice(unit) });
        return idx;
    };

    // Check which samplers were enabled by the user
    if (type_mask & ProfileType::CPU) {
        val_idx.cpu_time = get_value_idx("cpu-time", "nanoseconds");
        val_idx.cpu_count = get_value_idx("cpu-samples", "count");
    }
    if (type_mask & ProfileType::Wall) {
        val_idx.wall_time = get_value_idx("wall-time", "nanoseconds");
        val_idx.wall_count = get_value_idx("wall-samples", "count");
    }
    if (type_mask & ProfileType::Exception) {
        val_idx.exception_count = get_value_idx("exception-samples", "count");
    }
    if (type_mask & ProfileType::LockAcquire) {
        val_idx.lock_acquire_time = get_value_idx("lock-acquire-wait", "nanoseconds");
        val_idx.lock_acquire_count = get_value_idx("lock-acquire", "count");
    }
    if (type_mask & ProfileType::LockRelease) {
        val_idx.lock_release_time = get_value_idx("lock-release-hold", "nanoseconds");
        val_idx.lock_release_count = get_value_idx("lock-release", "count");
    }
    if (type_mask & ProfileType::Allocation) {
        val_idx.alloc_space = get_value_idx("alloc-space", "bytes");
        val_idx.alloc_count = get_value_idx("alloc-samples", "count");
    }
    if (type_mask & ProfileType::Heap) {
        val_idx.heap_space = get_value_idx("heap-space", "bytes");
    }

    values.resize(samplers.size());
    std::fill(values.begin(), values.end(), 0);

    ddog_prof_Period default_period = { .type_ = samplers[0], .value = 1 }; // Mandated by pprof, but probably unused
    ddog_prof_Slice_ValueType sample_types = { .ptr = samplers.data(), .len = samplers.size() };
    ddog_prof_Profile_NewResult res = ddog_prof_Profile_new(sample_types, &default_period, nullptr);

    // Check that the profile was created properly
    if (res.tag != DDOG_PROF_PROFILE_NEW_RESULT_OK) {
        std::string ddog_err(ddog_Error_message(&res.err).ptr);
        errmsg = "Could not create profile, err: " + ddog_err;
        ddog_Error_drop(&res.err);
        throw std::runtime_error(errmsg);
        return;
    }
    ddog_profile = res.ok;

    // Initialize storage
    locations.reserve(max_nframes + 1); // +1 for a "truncated frames" virtual frame

    // Prepare for use
    reset();
}

Profile::~Profile()
{
    ddog_prof_Profile_drop(&ddog_profile);
}

std::string_view
Profile::insert_or_get(std::string_view sv)
{
    auto it = strings.find(sv);
    if (it != strings.end()) {
        return *it;
    } else {
        string_storage.emplace_back(sv);
        strings.insert(string_storage.back());
        return string_storage.back();
    }
}

bool
Profile::reset()
{
    auto res = ddog_prof_Profile_reset(&ddog_profile, nullptr);
    if (!res.ok) {
        std::string ddog_err(ddog_Error_message(&res.err).ptr);
        errmsg = "Could not reset profile, err: " + ddog_err;
        ddog_Error_drop(&res.err);
        std::cout << errmsg << std::endl;
        return false;
    }
    return true;
}

bool
Profile::start_sample()
{
    // NB, since string_storage is a deque, `clear()` may not return all of
    // the allocated space
    strings.clear();
    string_storage.clear();
    clear_buffers();
    return true;
}

void
Profile::push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{
    static const ddog_prof_Mapping null_mapping = { 0, 0, 0, to_slice(""), to_slice("") };
    name = insert_or_get(name);
    filename = insert_or_get(filename);

    ddog_prof_Location loc = {
        null_mapping, // No support for mappings in Python
        {
          .name = to_slice(name),
          .system_name = {}, // No support for system name in Python
          .filename = to_slice(filename),
          .start_line = 0 // We don't know the start_line in the typical case
        },
        .address = address,
        .line = line,
    };
    locations.push_back(loc);
}

void
Profile::push_frame(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{

    if (locations.size() < max_nframes)
        push_frame_impl(name, filename, address, line);
}

bool
Profile::push_label(const ExportLabelKey key, std::string_view val)
{
    // libdatadog checks the labels when they get flushed, which slightly
    // de-localizes the error message.  Roll with it for now.
    constexpr std::array<std::string_view, static_cast<size_t>(ExportLabelKey::_Length)> keys = { EXPORTER_LABELS(
      X_STR) };
    if (cur_label >= labels.size()) {
        std::cout << "Bad push_label" << std::endl;
        return false;
    }

    // Label may not persist, so it needs to be saved
    std::string_view key_sv = keys[static_cast<size_t>(key)];
    val = insert_or_get(val);

    labels[cur_label].key = to_slice(key_sv);
    labels[cur_label].str = to_slice(val);
    cur_label++;
    return true;
}

bool
Profile::push_label(const ExportLabelKey key, int64_t val)
{
    constexpr std::array<std::string_view, static_cast<size_t>(ExportLabelKey::_Length)> keys = { EXPORTER_LABELS(
      X_STR) };
    if (cur_label >= labels.size()) {
        std::cout << "Bad push_label" << std::endl;
        return false;
    }

    std::string_view key_sv = keys[static_cast<size_t>(key)];
    labels[cur_label].key = to_slice(key_sv);
    labels[cur_label].num = val;
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
    // We choose to normalize thread counts against the user's indicated
    // preference, even though we have no control over how many frames are sent.
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

    ddog_prof_Profile_Result res = ddog_prof_Profile_add(&ddog_profile, sample, 0);
    if (res.tag == DDOG_PROF_PROFILE_RESULT_ERR) {
        std::string ddog_errmsg(ddog_Error_message(&res.err).ptr);
        std::cout << "'" << ddog_errmsg << "'" << std::endl;
        errmsg = "Could not flush sample: " + ddog_errmsg;
        std::cout << errmsg << std::endl;
        ddog_Error_drop(&res.err);
        clear_buffers();
        return false;
    }

    clear_buffers();
    return true;
}

bool
Profile::push_cputime(int64_t cputime, int64_t count)
{
    // NB all push-type operations return bool for semantic uniformity,
    // even if they can't error.  This should promote generic code.
    if (type_mask & ProfileType::CPU) {
        values[val_idx.cpu_time] += cputime * count;
        values[val_idx.cpu_count] += count;
        return true;
    }
    std::cout << "bad push cpu" << std::endl;
    return false;
}

bool
Profile::push_walltime(int64_t walltime, int64_t count)
{
    if (type_mask & ProfileType::Wall) {
        values[val_idx.wall_time] += walltime * count;
        values[val_idx.wall_count] += count;
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
        values[val_idx.exception_count] += count;
        return true;
    }
    std::cout << "bad push except" << std::endl;
    return false;
}

bool
Profile::push_acquire(int64_t acquire_time, int64_t count)
{
    if (type_mask & ProfileType::LockAcquire) {
        values[val_idx.lock_acquire_time] += acquire_time;
        values[val_idx.lock_acquire_count] += count;
        return true;
    }
    std::cout << "bad push acquire" << std::endl;
    return false;
}

bool
Profile::push_release(int64_t release_time, int64_t count)
{
    if (type_mask & ProfileType::LockRelease) {
        values[val_idx.lock_release_time] += release_time;
        values[val_idx.lock_release_count] += count;
        return true;
    }
    std::cout << "bad push release" << std::endl;
    return false;
}

bool
Profile::push_alloc(uint64_t size, uint64_t count)
{
    if (type_mask & ProfileType::Allocation) {
        values[val_idx.alloc_space] += size;
        values[val_idx.alloc_count] += count;
        return true;
    }
    std::cout << "bad push alloc" << std::endl;
    return false;
}

bool
Profile::push_heap(uint64_t size)
{
    if (type_mask & ProfileType::Heap) {
        values[val_idx.heap_space] += size;
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
