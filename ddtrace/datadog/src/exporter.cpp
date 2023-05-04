// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

// High-level skip for invalid architectures
#ifndef __linux__
#elif __aarch64__
#elif __i386__
#else

#include "exporter.hpp"

#include <iostream>

#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

using namespace Datadog;

inline ddog_CharSlice to_slice(std::string_view str) {
  return {.ptr = str.data(), .len = str.size()};
}

DdogProfExporter::DdogProfExporter(std::string_view env,
                                   std::string_view service,
                                   std::string_view version, 
                                   std::string_view runtime,
                                   std::string_view runtime_version,
                                   std::string_view profiler_version,
                                   std::string_view url,
                                   ExporterTagset &user_tags) {

  // Setup
  ddog_Vec_Tag tags = ddog_Vec_Tag_new();
  add_tag(tags, ExportTagKey::language, language);
  add_tag(tags, ExportTagKey::env, env);
  add_tag(tags, ExportTagKey::service, service);
  add_tag(tags, ExportTagKey::version, version);
  add_tag(tags, ExportTagKey::runtime, runtime);
  add_tag(tags, ExportTagKey::runtime_version, runtime_version);
  add_tag(tags, ExportTagKey::profiler_version, profiler_version);

  // Add the unsafe tags, if any
  for (const auto &kv : user_tags)
    add_tag_unsafe(tags, kv.first, kv.second);

  ddog_prof_Exporter_NewResult new_exporter = ddog_prof_Exporter_new(
      to_slice("dd-trace-py"),
      to_slice(profiler_version),
      to_slice(family),
      &tags,
      ddog_Endpoint_agent(to_slice(url)));
  ddog_Vec_Tag_drop(tags);

  if (new_exporter.tag == DDOG_PROF_EXPORTER_NEW_RESULT_OK) {
    ptr = new_exporter.ok;
  } else {
    // TODO consolidate errors
    std::cout << "ERROR INITIALIZING LIBDATADOG EXPORTER" << std::endl;
    ddog_Error_drop(&new_exporter.err);
  }
}

DdogProfExporter::~DdogProfExporter() {
  ddog_prof_Exporter_drop(ptr);
}

UploaderBuilder::UploaderBuilder(std::string_view _env,
                                 std::string_view _service,
                                 std::string_view _version,
                                 std::string_view _runtime,
                                 std::string_view _runtime_version,
                                 std::string_view _profiler_version,
                                 std::string_view _url) :
  env{_env},
  service{_service},
  version{_version},
  runtime{_runtime},
  runtime_version{_runtime_version},
  profiler_version{_profiler_version},
  url{_url}
{}

UploaderBuilder &UploaderBuilder::set_env(std::string_view env) {
  // Don't over-write the default with garbage
  if (env.empty())
    return *this;
  this->env = env;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_service(std::string_view service) {
  // Don't over-write the default with garbage
  if (service.empty())
    return *this;
  this->service = service;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_version(std::string_view version) {
  this->version = version;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_runtime(std::string_view runtime) {
  this->runtime = runtime;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_runtime_version(std::string_view runtime_version) {
  this->runtime_version = runtime_version;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_profiler_version(std::string_view profiler_version) {
  this->profiler_version = profiler_version;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_url(std::string_view url) {
  this->url = url;
  return *this;
}
UploaderBuilder &UploaderBuilder::set_tag(std::string_view key, std::string_view val) {
  if (key.empty() || val.empty())
    return *this;
  user_tags[key] = val;
  return *this;
}

Uploader *UploaderBuilder::build_ptr() {
  return new Uploader(env, service, version, runtime, runtime_version, profiler_version, url, user_tags);
}

Uploader::Uploader(std::string_view env,
                   std::string_view service,
                   std::string_view version,
                   std::string_view runtime,
                   std::string_view runtime_version,
                   std::string_view profiler_version,
                   std::string_view url,
                   DdogProfExporter::ExporterTagset &user_tags) {
  this->url = url;
  ddog_exporter = std::make_unique<DdogProfExporter>(env, service, version, runtime, runtime_version, profiler_version, url, user_tags);
}

void Uploader::set_runtime_id(const std::string &id) {
  runtime_id = id;
}

#define X_STR(a, b) b,
void DdogProfExporter::add_tag(ddog_Vec_Tag &tags, const ExportTagKey key, std::string_view val) {
  // NB the storage of `val` needs to be guaranteed until the tags are flushed
  constexpr std::array<std::string_view, static_cast<size_t>(ExportTagKey::_Length)> keys = {
    EXPORTER_TAGS(X_STR)
  };

  // Input check
  if (val.empty())
    return;

  // Add
  std::string_view key_sv = keys[static_cast<size_t>(key)];
  ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key_sv), to_slice(val));
  if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
    // TODO consolidate errors
    std::cout << "Error pushing tag '" << key_sv << "'->'" << val << "'" << std::endl;
    std::cout << "  err: " << ddog_Error_message(&res.err).ptr << std::endl;
    ddog_Error_drop(&res.err);
  }
}

void DdogProfExporter::add_tag_unsafe(ddog_Vec_Tag &tags, std::string_view key, std::string_view val) {
  if (key.empty() || val.empty())
    return;
  ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key), to_slice(val));
  if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
    // TODO consolidate errors
    std::cout << "Error pushing (unsafe) tag '" << key << "'->'" << val << "'" << std::endl;
    std::cout << "  err: " << ddog_Error_message(&res.err).ptr << std::endl;
    ddog_Error_drop(&res.err);
  }
}

bool Uploader::upload(const Profile *profile) {
  ddog_prof_Profile_SerializeResult result = ddog_prof_Profile_serialize(
      profile->ddog_profile,
      nullptr,
      nullptr);
  if (result.tag != DDOG_PROF_PROFILE_SERIALIZE_RESULT_OK) {
    // TODO consolidate errors
    std::cout << "Failure serializing pprof" << std::endl;
    std::cout << "  err: " << ddog_Error_message(&result.err).ptr << std::endl;
    ddog_Error_drop(&result.err);
    return false;
  }

  ddog_prof_EncodedProfile *encoded = &result.ok;

  ddog_Timespec start = encoded->start;
  ddog_Timespec end = encoded->end;

  // Attach file
  ddog_prof_Exporter_File file[] = {
    {
      .name = to_slice("auto.pprof"),
      .file = {
        .ptr = encoded->buffer.ptr,
        .len = encoded->buffer.len,
      },
    },
  };

  // If we have any custom tags, set them now
  ddog_Vec_Tag tags = ddog_Vec_Tag_new();
  ddog_exporter->add_tag(tags, ExportTagKey::profile_seq, std::to_string(profile_seq++));
  ddog_exporter->add_tag(tags, ExportTagKey::runtime_id, runtime_id);

  // Build the request object
  ddog_prof_Exporter_Request_BuildResult build_res = ddog_prof_Exporter_Request_build(
      ddog_exporter->ptr,
      start,
      end,
      {.ptr = file, .len = 1},
      &tags,
      nullptr,
      5000);

  if (build_res.tag == DDOG_PROF_EXPORTER_REQUEST_BUILD_RESULT_ERR) {
    // TODO consolidate errors
    std::cout << "Could not build request" << std::endl;
    std::cout << "  " << ddog_Error_message(&build_res.err).ptr << std::endl;
    ddog_Error_drop(&build_res.err);
    ddog_prof_EncodedProfile_drop(encoded);
    ddog_Vec_Tag_drop(tags);
    return false;
  }

  // Build and check the response object
  ddog_prof_Exporter_Request *req = build_res.ok;
  ddog_prof_Exporter_SendResult res = ddog_prof_Exporter_send(ddog_exporter->ptr, &req, nullptr);
  if (res.tag == DDOG_PROF_EXPORTER_SEND_RESULT_ERR) {
    // TODO consolidate errors
    std::cout << "Failed to send result to backend" << std::endl;
    std::cout << "  url: " <<  url << std::endl;
    std::cout << "  " << ddog_Error_message(&res.err).ptr << std::endl;
    ddog_Error_drop(&res.err);
    ddog_prof_EncodedProfile_drop(encoded);
    ddog_Vec_Tag_drop(tags);
    return false;
  }

  // Cleanup
  // TODO which of these can be moved closer to point of allocation?
  ddog_prof_Exporter_Request_drop(&req);
  ddog_prof_EncodedProfile_drop(encoded);
  ddog_Vec_Tag_drop(tags);

  return true;
}

ProfileBuilder::ProfileBuilder(Profile::ProfileType _type_mask, unsigned int _max_nframes) : 
  type_mask{Profile::ProfileType::All & _type_mask},
  max_nframes{_max_nframes}
{}

ProfileBuilder &ProfileBuilder::add_type(Profile::ProfileType type) {
  unsigned int mask_as_int = (type_mask | type) & Profile::ProfileType::All;
  type_mask = static_cast<Profile::ProfileType>(mask_as_int);
  return *this;
}

ProfileBuilder &ProfileBuilder::add_type(unsigned int type) {
  return add_type(static_cast<Profile::ProfileType>(type));
}

ProfileBuilder &ProfileBuilder::set_max_nframes(unsigned int max_nframes) {
  this->max_nframes = max_nframes;
  return *this;
}

Profile *ProfileBuilder::build_ptr() {
  return new Profile(type_mask, max_nframes);
}

Profile::Profile(ProfileType type = ProfileType::All, unsigned int _max_nframes) :
  type_mask{type & ProfileType::All},
  max_nframes{_max_nframes}
  {

  // Check for invalid type
  if (!type_mask)
    return;

  // Push an element to the end of the vector, returning the position of insertion
  std::vector<ddog_prof_ValueType> samplers;
  auto get_value_idx= [&samplers](std::string_view value, std::string_view unit) {
      size_t idx = samplers.size();
      samplers.push_back({to_slice(value), to_slice(unit)});
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

  if (!samplers.size()) {
    // TODO consolidate errors
    return;
  }
  values.resize(samplers.size());
  std::fill(values.begin(), values.end(), 0);

  ddog_prof_Period default_sampler = {samplers[0], 1}; // Mandated by pprof, but probably unused
  ddog_profile = ddog_prof_Profile_new(
      {&samplers[0], samplers.size()},
      &default_sampler,
      nullptr);

  // Prepare for use
  reset();

  // Initialize the size for buffers
  locations.reserve(2040);
  lines.reserve(2048);
  strings.reserve(8192);
}

Profile::~Profile() {
  ddog_prof_Profile_drop(ddog_profile);
}

void Profile::reset() {
  // TODO consolidate errors
  if (!ddog_prof_Profile_reset(ddog_profile, nullptr))
    std::cout << "Unable to reset!" << std::endl;
}

void Profile::start_sample(unsigned int nframes) {
  strings.clear();
  clear_buffers();
  this->nframes = nframes;
}

void Profile::push_frame_impl(
    std::string_view name,
    std::string_view filename,
    uint64_t address,
    int64_t line) {

  // Ensure strings are stored.
  // Slightly wasteful since it requires allocating another string.
  auto insert_or_get = [&](std::string_view sv) -> std::string_view {
    std::string str(sv);
    auto [it, _] = strings.insert(std::move(str));
    return *it;
  };
  name = insert_or_get(name);
  filename = insert_or_get(filename);

  lines.push_back({
          .function = {
            .name = to_slice(name),
            .system_name = {},
            .filename = to_slice(filename),
            .start_line = 0,
            },
          .line = line,
  });

  locations.push_back({
       {},
       address,
       {&lines.back(), 1},
       false,
  });
}

void Profile::push_frame(
    std::string_view name,
    std::string_view filename,
    uint64_t address,
    int64_t line) {

  push_frame_impl(name, filename, address, line);
}

void Profile::push_label(const ExportLabelKey key, std::string_view val) {
  constexpr std::array<std::string_view, static_cast<size_t>(ExportLabelKey::_Length)> keys = {
    EXPORTER_LABELS(X_STR)
  };
  std::string_view key_sv = keys[static_cast<size_t>(key)];
  labels[cur_label].key = to_slice(key_sv);

  // Label may not persist, so it needs to be saved
  auto [it, _] = strings.insert(std::string{val});
  labels[cur_label].str = to_slice(*it);
  cur_label++;
}

void Profile::push_label(const ExportLabelKey key, int64_t val) {
  constexpr std::array<std::string_view, static_cast<size_t>(ExportLabelKey::_Length)> keys = {
    EXPORTER_LABELS(X_STR)
  };
  std::string_view key_sv = keys[static_cast<size_t>(key)];
  labels[cur_label].key = to_slice(key_sv);
  labels[cur_label].num = val;
  cur_label++;
}

void Profile::clear_buffers() {
  locations.clear();
  lines.clear();
  std::fill(values.begin(), values.end(), 0);
  std::fill(std::begin(labels), std::end(labels), ddog_prof_Label{});
  cur_label = 0;
  nframes = 0;
}

bool Profile::flush_sample() {
  // We choose to normalize thread counts against the user's indicated preference, even though we have no
  // control over how many frames are sent.
  if (nframes > max_nframes) {
    auto dropped_frames = nframes - max_nframes;
    std::string name = "<" + std::to_string(dropped_frames) + " frame" + (1 == dropped_frames ? "" : "s") + " omitted>";
    Profile::push_frame_impl(name, "", 0, 0);
  }

  ddog_prof_Sample sample = {
    .locations = {&locations[0], locations.size()},
    .values = {&values[0], values.size()},
    .labels = {labels, cur_label},
  };

  ddog_prof_Profile_AddResult address = ddog_prof_Profile_add(ddog_profile, sample);

  if (address.tag == DDOG_PROF_PROFILE_ADD_RESULT_ERR) {
    std::cout << ddog_Error_message(&address.err).ptr << std::endl;
    ddog_Error_drop(&address.err);

    std::cout << "Problem flushing sample to profile" << std::endl;
    for (auto el : values) {
      std::cout << el << ", ";
    }
    std::cout << std::endl;
    for (auto el : locations) {
      for (size_t i = 0; i < el.lines.len; ++i) {
        std::cout << "Pulling location " << i + 1 << " of " << el.lines.len << std::endl;
        const ddog_prof_Line *ell = &el.lines.ptr[i];
        std::cout << "  line is " << ell->line << std::endl;
        if (!ell) {
          std::cout << "  Location is null" << std::endl;
        }
        const char *name = ell->function.name.ptr;
        if (!name)
          std::cout << "  name is NULL" << std::endl;
        else
          std::cout << "  name: " << std::string(name) << std::endl;

        const char *sname = ell->function.system_name.ptr;
        if (!sname)
          std::cout << "  sname is NULL" << std::endl;
        else
          std::cout << "  sname: " << std::string() << std::endl;

        const char *filename = ell->function.filename.ptr;
        if (!filename)
          std::cout << "  file is NULL" << std::endl;
        else
          std::cout << "  file: " << std::string(filename) << std::endl;

        std::cout << "  fline: " << ell->function.start_line << std::endl;
        std::cout << std::endl;
      }
    }

    std::cout << "  labels: " << std::endl;
    for (size_t i = 0; i < cur_label; i++) {
      auto &el = labels[i];
      std::cout << "  key: `" << el.key.ptr << "`" << std::endl;
      if (el.str.ptr)
        std::cout << "  val: `" << el.str.ptr << "`" << std::endl;
      else
        std::cout << "  num: `" << el.num << "`" << std::endl;
      std::cout << std::endl;
    }
    clear_buffers();
    std::exit(-1);
    return false;
  }
  clear_buffers();
  return true;
}

void Datadog::Profile::push_cputime(int64_t cputime, int64_t count) {
  if (type_mask & ProfileType::CPU) {
    values[val_idx.cpu_time] += cputime * count;
    values[val_idx.cpu_count] += count;
  }
}

void Datadog::Profile::push_walltime(int64_t walltime, int64_t count) {
  if (type_mask & ProfileType::Wall) {
    values[val_idx.wall_time] += walltime * count;
    values[val_idx.wall_count] += count;
  }
}

void Datadog::Profile::push_exceptioninfo(std::string_view exception_type, int64_t count) {
  if (type_mask & ProfileType::Exception) {
    push_label(ExportLabelKey::exception_type, exception_type);
    values[val_idx.exception_count] += count;
  }
}

void Datadog::Profile::push_acquire(int64_t acquire_time, int64_t count) {
  if (type_mask & ProfileType::LockAcquire) {
    values[val_idx.lock_acquire_time] += acquire_time;
    values[val_idx.lock_acquire_count] += count;
  }
}

void Datadog::Profile::push_release(int64_t release_time, int64_t count) {
  if (type_mask & ProfileType::LockRelease) {
    values[val_idx.lock_release_time] += release_time;
    values[val_idx.lock_release_count] += count;
  }
}

void Datadog::Profile::push_alloc(uint64_t size, uint64_t count) {
  if (type_mask & ProfileType::Allocation) {
    values[val_idx.alloc_space] += size;
    values[val_idx.alloc_count] += count;
  }
}

void Datadog::Profile::push_heap(uint64_t size) {
  if (type_mask & ProfileType::Heap) {
    values[val_idx.heap_space] += size;
  }
}

void Datadog::Profile::push_lock_name(std::string_view lock_name) {
  push_label(ExportLabelKey::lock_name, lock_name);
}

void Datadog::Profile::push_threadinfo(int64_t thread_id, int64_t thread_native_id, std::string_view thread_name) {
  // TODO ensure the thread ids do have transparently static storage
  push_label(ExportLabelKey::thread_id, thread_id);
  push_label(ExportLabelKey::thread_native_id, thread_native_id);
  push_label(ExportLabelKey::thread_name, thread_name);
}

void Datadog::Profile::push_taskinfo(int64_t task_id, std::string_view task_name) {
  push_label(ExportLabelKey::task_id, task_id);
  push_label(ExportLabelKey::task_name, task_name);
}

void Datadog::Profile::push_span_id(int64_t span_id) {
  push_label(ExportLabelKey::span_id, span_id);
}

void Datadog::Profile::push_local_root_span_id(int64_t local_root_span_id) {
  push_label(ExportLabelKey::local_root_span_id, local_root_span_id);
}

void Datadog::Profile::push_trace_type(std::string_view trace_type) {
  push_label(ExportLabelKey::trace_type, trace_type);
}

void Datadog::Profile::push_trace_resource_container(std::string_view trace_resource_container) {
  push_label(ExportLabelKey::trace_resource_container, trace_resource_container);
}

void Datadog::Profile::push_class_name(std::string_view class_name) {
  push_label(ExportLabelKey::class_name, class_name);
}

#endif
