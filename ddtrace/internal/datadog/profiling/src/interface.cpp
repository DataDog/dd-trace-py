// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

// High-level skip for invalid architectures
#ifndef __linux__
#elif __aarch64__
#elif __i386__
#else

#include "interface.hpp"
#include "exporter.hpp"

#include <iostream>
#include <thread>

// State
bool is_initialized = false;
Datadog::Uploader *g_uploader;
Datadog::Profile *g_profile;
Datadog::Profile *g_profile_real[2];
bool g_prof_flag = true;

// State used only for one-time configuration
Datadog::UploaderBuilder uploader_builder;
Datadog::ProfileBuilder profile_builder;

// Configuration
void ddup_config_env(const char *env) {
  if (!env || !*env)
    env = "prod";
  uploader_builder.set_env(env);
}
void ddup_config_service(const char *service) {
  if (!service || !*service)
  uploader_builder.set_service(service);
}
void ddup_config_version(const char *version) {
  if (!version || !*version)
    return;
  uploader_builder.set_version(version);
}
void ddup_config_runtime(const char *runtime) {
  uploader_builder.set_runtime(runtime);
}
void ddup_config_runtime_version(const char *runtime_version) {
  uploader_builder.set_runtime_version(runtime_version);
}
void ddup_config_profiler_version(const char *profiler_version) {
  uploader_builder.set_profiler_version(profiler_version);
}
void ddup_config_url(const char *url) { uploader_builder.set_url(url); }
void ddup_config_user_tag(const char *key, const char *val) {
  uploader_builder.set_tag(key, val);
}
void ddup_config_sample_type(unsigned int type) {
  profile_builder.add_type(type);
}
void ddup_config_max_nframes(int max_nframes) {
  if (max_nframes > 0)
    profile_builder.set_max_nframes(max_nframes);
}

// Initialization
void ddup_init() {
  if (!is_initialized) {
    g_profile_real[0] = profile_builder.build_ptr();
    g_profile_real[1] = profile_builder.build_ptr();
    g_profile = g_profile_real[g_prof_flag];
    g_uploader = uploader_builder.build_ptr();
    is_initialized = true;
  }
}

void ddup_start_sample(unsigned int nframes) {
  g_profile->start_sample(nframes);
}

void ddup_push_walltime(int64_t walltime, int64_t count) {
  g_profile->push_walltime(walltime, count);
}

void ddup_push_cputime(int64_t cputime, int64_t count) {
  g_profile->push_cputime(cputime, count);
}

void ddup_push_acquire(int64_t acquire_time, int64_t count) {
  g_profile->push_acquire(acquire_time, count);
}

void ddup_push_release(int64_t release_time, int64_t count) {
  g_profile->push_release(release_time, count);
}

void ddup_push_alloc(uint64_t size, uint64_t count) {
  g_profile->push_alloc(size, count);
}

void ddup_push_heap(uint64_t size) { g_profile->push_heap(size); }

void ddup_push_lock_name(const char *lock_name) {
  if (!lock_name)
    return;
  g_profile->push_lock_name(lock_name);
}

void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id,
                          const char *thread_name) {
  if (!thread_name)
    return;
  g_profile->push_threadinfo(thread_id, thread_native_id, thread_name);
}

void ddup_push_taskinfo(int64_t task_id, const char *task_name) {
  if (!task_name)
    return;
  g_profile->push_taskinfo(task_id, task_name);
}

void ddup_push_span_id(int64_t span_id) { g_profile->push_span_id(span_id); }

void ddup_push_local_root_span_id(int64_t local_root_span_id) {
  g_profile->push_local_root_span_id(local_root_span_id);
}

void ddup_push_trace_type(const char *trace_type) {
  if (!trace_type || !*trace_type)
    return;
  g_profile->push_trace_type(trace_type);
}

void ddup_push_trace_resource_container(const char *trace_resource_container) {
  if (!trace_resource_container || !*trace_resource_container)
    return;
  g_profile->push_trace_resource_container(trace_resource_container);
}

void ddup_push_exceptioninfo(const char *exception_type, int64_t count) {
  if (!exception_type || !count)
    return;
  g_profile->push_exceptioninfo(exception_type, count);
}

void ddup_push_class_name(const char *class_name) {
  if (!class_name)
    return;
  g_profile->push_class_name(class_name);
}

void ddup_push_frame(const char *name, const char *fname, uint64_t address,
                     int64_t line) {
  g_profile->push_frame(name, fname, address, line);
}

void ddup_flush_sample() { g_profile->flush_sample(); }

void ddup_set_runtime_id(const char *id) { g_uploader->set_runtime_id(id); }

void ddup_upload_impl(Datadog::Profile *prof) { g_uploader->upload(prof); }

void ddup_upload() {
  if (!is_initialized)
    std::cout << "WHOA NOT INITIALIZED" << std::endl;
  new std::thread(ddup_upload_impl, g_profile); // set it and forget it
  g_prof_flag ^= true;
  g_profile = g_profile_real[g_prof_flag];
  g_profile->reset();
}

#endif
