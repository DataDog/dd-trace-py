// Unless explicitly stated otherwise all files in this repository are licensed
// under the Apache License Version 2.0. This product includes software
// developed at Datadog (https://www.datadoghq.com/). Copyright 2021-Present
// Datadog, Inc.

#pragma once

#include "exporter.hpp"

extern "C" {
void ddup_config_env(const char *env);
void ddup_config_service(const char *service);
void ddup_config_version(const char *version);
void ddup_config_runtime(const char *runtime);
void ddup_config_runtime_version(const char *runtime_version);
void ddup_config_profiler_version(const char *profiler_version);
void ddup_config_url(const char *url);
void ddup_config_max_nframes(int max_nframes);

void ddup_config_user_tag(const char *key, const char *val);
void ddup_config_sample_type(unsigned int type);

void ddup_init();

void ddup_start_sample(unsigned int nframes);
void ddup_push_walltime(int64_t walltime, int64_t count);
void ddup_push_cputime(int64_t cputime, int64_t count);
void ddup_push_acquire(int64_t acquire_time, int64_t count);
void ddup_push_release(int64_t release_time, int64_t count);
void ddup_push_alloc(uint64_t size, uint64_t count);
void ddup_push_heap(uint64_t size);
void ddup_push_lock_name(const char *lock_name);
void ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id,
                          const char *thread_name);
void ddup_push_task_id(int64_t task_id);
void ddup_push_task_name(const char *task_name);
void ddup_push_span_id(int64_t span_id);
void ddup_push_local_root_span_id(int64_t local_root_span_id);
void ddup_push_trace_type(const char *trace_type);
void ddup_push_trace_resource_container(const char *trace_resource_container);
void ddup_push_exceptioninfo(const char *exception_type, int64_t count);
void ddup_push_class_name(const char *class_name);
void ddup_push_frame(const char *_name, const char *_filename, uint64_t address,
                     int64_t line);
void ddup_flush_sample();
void ddup_set_runtime_id(const char *id, size_t sz);
void ddup_upload();
} // extern "C"
