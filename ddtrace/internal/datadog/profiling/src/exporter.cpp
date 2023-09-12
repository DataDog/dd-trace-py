// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.
#include "exporter.hpp"
#include <iostream>

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
UploaderBuilder::set_env(std::string_view env)
{
    if (!env.empty())
        this->env = env;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_service(std::string_view service)
{
    if (!service.empty())
        this->service = service;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_version(std::string_view version)
{
    if (!version.empty())
        this->version = version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_runtime(std::string_view runtime)
{
    this->runtime = runtime;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_runtime_version(std::string_view runtime_version)
{
    this->runtime_version = runtime_version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_profiler_version(std::string_view profiler_version)
{
    this->profiler_version = profiler_version;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_url(std::string_view url)
{
    this->url = url;
    return *this;
}
UploaderBuilder&
UploaderBuilder::set_tag(std::string_view key, std::string_view val)
{
    if (key.empty() || val.empty())
        return *this;
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
    ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(profile->ddog_profile, nullptr, nullptr);
    if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
        std::string ddog_errmsg(ddog_Error_message(&result.err).ptr);
        errmsg = "Error serializing pprof, err:" + ddog_errmsg;
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
          .file =
              {
                  .ptr = encoded->buffer.ptr,
                  .len = encoded->buffer.len,
              },
      },
  };

    // If we have any custom tags, set them now
    ddog_Vec_Tag tags = ddog_Vec_Tag_new();
    add_tag(tags, ExportTagKey::profile_seq, std::to_string(profile_seq++), errmsg);
    add_tag(tags, ExportTagKey::runtime_id, runtime_id, errmsg);

    // Build the request object
    ddog_prof_Exporter_Request_BuildResult build_res = ddog_prof_Exporter_Request_build(
      ddog_exporter.get(), start, end, { .ptr = file, .len = 1 }, &tags, nullptr, 5000);

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
ProfileBuilder::set_max_nframes(unsigned int max_nframes)
{
    this->max_nframes = max_nframes;
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
    std::vector<ddog_prof_ValueType> samplers;
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

    ddog_prof_Period default_sampler = { samplers[0], 1 }; // Mandated by pprof, but probably unused
    ddog_profile = ddog_prof_Profile_new({ &samplers[0], samplers.size() }, &default_sampler, nullptr);

    // Prepare for use
    reset();

    // Initialize the size for buffers
    cur_frame = 0;
}

Profile::~Profile()
{
    ddog_prof_Profile_drop(ddog_profile);
}

bool
Profile::reset()
{
    if (!ddog_prof_Profile_reset(ddog_profile, nullptr)) {
        errmsg = "Unable to reset profile";
        std::cout << errmsg << std::endl;
        return false;
    }
    return true;
}

bool
Profile::start_sample(unsigned int nframes)
{
    strings.clear();
    string_storage.clear();
    clear_buffers();
    this->nframes = nframes;
    return true;
}

void
Profile::push_frame_impl(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{
    if (cur_frame >= lines.size() || cur_frame >= locations.size())
        return;

    // Ensure strings are stored.
    auto insert_or_get = [this](std::string_view sv) -> std::string_view {
        auto it = strings.find(sv);
        if (it != strings.end()) {
            return *it;
        } else {
            string_storage.emplace_back(sv);
            strings.insert(string_storage.back());
            return string_storage.back();
        }
    };
    name = insert_or_get(name);
    filename = insert_or_get(filename);

    lines[cur_frame] = {
      .function =
          {
              .name = to_slice(name),
              .system_name = {},
              .filename = to_slice(filename),
              .start_line = 0,
          },
      .line = line,
  };

    locations[cur_frame] = {
        {},
        address,
        { &lines[cur_frame], 1 },
        false,
    };

    ++cur_frame;
}

void
Profile::push_frame(std::string_view name, std::string_view filename, uint64_t address, int64_t line)
{

    if (cur_frame <= max_nframes)
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

    // Ensure strings are stored
    auto insert_or_get = [this](std::string_view sv) -> std::string_view {
        auto it = strings.find(sv);
        if (it != strings.end()) {
            return *it;
        } else {
            string_storage.emplace_back(sv);
            strings.insert(string_storage.back());
            return string_storage.back();
        }
    };

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
    cur_label = 0;
    cur_frame = 0;
    nframes = 0;
}

bool
Profile::flush_sample()
{
    // We choose to normalize thread counts against the user's indicated
    // preference, even though we have no control over how many frames are sent.
    if (nframes > max_nframes) {
        auto dropped_frames = nframes - max_nframes;
        std::string name =
          "<" + std::to_string(dropped_frames) + " frame" + (1 == dropped_frames ? "" : "s") + " omitted>";
        Profile::push_frame_impl(name, "", 0, 0);
    }

    ddog_prof_Sample sample = {
        .locations = { &locations[0], cur_frame },
        .values = { &values[0], values.size() },
        .labels = { &labels[0], cur_label },
    };

    ddog_prof_Profile_AddResult address = ddog_prof_Profile_add(ddog_profile, sample);
    if (address.tag == DDOG_PROF_PROFILE_ADD_RESULT_ERR) {
        std::string ddog_errmsg(ddog_Error_message(&address.err).ptr);
        errmsg = "Could not flush sample: " + errmsg;
        ddog_Error_drop(&address.err);

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
Profile::push_span_id(int64_t span_id)
{
    if (!push_label(ExportLabelKey::span_id, span_id)) {
        std::cout << "bad push" << std::endl;
        return false;
    }
    return true;
}

bool
Profile::push_local_root_span_id(int64_t local_root_span_id)
{
    if (!push_label(ExportLabelKey::local_root_span_id, local_root_span_id)) {
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
