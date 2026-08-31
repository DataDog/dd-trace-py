//! Option C, Stage 0/1: PyO3 data structs for the profiling hot path, mirroring
//! `dd_wrapper`'s `Sample`/`Profile`/`ProfilerState` (see
//! `OPTION_C_ONE_HOP_PLAN.md`) directly onto `libdd-profiling`'s safe Rust API --
//! no `dd_wrapper` C++, no `ddog_prof_*` C ABI.
//!
//! Mirrors `dd_wrapper`'s `Sample` (`sample.hpp`) push-by-push, using
//! `ProfilesDictionary`/`api2` (the same interning system `sample.cpp` already
//! calls via the C ABI: `ddog_prof_ProfilesDictionary_insert_str`/`_insert_function`,
//! `ddog_prof_Sample2`) instead of `sample.cpp`'s own `StringArena`.
//!
//! Frame strings (function name/filename) are interned into the shared
//! `ProfilesDictionary` at push time via `try_insert_str2`/`try_insert_function2`,
//! which take `&self` -- interning does not need the profile mutex. The profile
//! mutex (`DdProfilerState::profile`) is only taken once, in `add_sample`, at
//! flush -- keeping the per-push path lock-free, matching `sample.cpp`.

use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};
use std::time::{Duration, SystemTime};

use parking_lot::Mutex;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use libdd_profiling::api;
use libdd_profiling::api2::{Label as Label2, Location2};
use libdd_profiling::internal::Profile;
use libdd_profiling::profiles::collections::Arc as DictArc;
use libdd_profiling::profiles::datatypes::{MappingId2, ProfilesDictionary, StringId2};

fn to_py_err(e: anyhow::Error) -> PyErr {
    PyValueError::new_err(e.to_string())
}

fn ns_to_systemtime(ns: i64) -> PyResult<SystemTime> {
    if ns >= 0 {
        SystemTime::UNIX_EPOCH
            .checked_add(Duration::from_nanos(ns as u64))
            .ok_or_else(|| PyValueError::new_err("timestamp overflowed SystemTime"))
    } else {
        SystemTime::UNIX_EPOCH
            .checked_sub(Duration::from_nanos((-ns) as u64))
            .ok_or_else(|| PyValueError::new_err("timestamp underflowed SystemTime"))
    }
}

/// Mirrors `Datadog::Sample::is_timeline_enabled()`/`set_timeline()`
/// (`sample.cpp`), which read/write a plain field on the `ProfilerState`
/// singleton. There's no singleton here yet (see plan Stage 3), so this is a
/// process-global flag for now; `push_monotonic_ns`/`push_absolute_ns` are
/// no-ops while it's unset, exactly like the C++ side.
static TIMELINE_ENABLED: AtomicBool = AtomicBool::new(false);

#[pyfunction]
pub fn set_timeline(enabled: bool) {
    TIMELINE_ENABLED.store(enabled, Ordering::Relaxed);
}

fn is_timeline_enabled() -> bool {
    TIMELINE_ENABLED.load(Ordering::Relaxed)
}

/// Mirrors `Datadog::get_monotonic_ns()` (`clock.hpp`) exactly, including its
/// platform split: `clock_gettime(CLOCK_MONOTONIC, ...)` on Linux,
/// `mach_absolute_time()` on macOS. These must agree with what Python's
/// `time.monotonic_ns()` uses on each platform, because `push_monotonic_ns`
/// computes a static offset between this clock and the wall epoch and
/// applies it to values Python passes in.
#[cfg(target_os = "macos")]
#[allow(deprecated)]
fn get_monotonic_ns() -> i64 {
    // macOS's `CLOCK_MONOTONIC` maps to `mach_continuous_time`, which
    // includes sleep time and diverges from `mach_absolute_time` (and thus
    // from Python's `time.monotonic_ns`) after system sleep -- must stay in
    // sync with `clock.hpp`'s `get_monotonic_ns()`.
    unsafe {
        let mut timebase: libc::mach_timebase_info = std::mem::zeroed();
        libc::mach_timebase_info(&mut timebase);
        let ticks = libc::mach_absolute_time();
        (ticks as u128 * timebase.numer as u128 / timebase.denom as u128) as i64
    }
}

#[cfg(not(target_os = "macos"))]
fn get_monotonic_ns() -> i64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe {
        libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts);
    }
    ts.tv_sec * 1_000_000_000 + ts.tv_nsec
}

/// Mirrors `Datadog::SampleType` (`types.hpp`) -- a bitmask of which sample
/// types this profiler instance is configured to record.
#[allow(non_snake_case)]
pub mod SampleTypeMask {
    pub const CPU: u16 = 1 << 0;
    pub const WALL: u16 = 1 << 1;
    pub const EXCEPTION: u16 = 1 << 2;
    pub const LOCK_ACQUIRE: u16 = 1 << 3;
    pub const LOCK_RELEASE: u16 = 1 << 4;
    pub const ALLOCATION: u16 = 1 << 5;
    pub const HEAP: u16 = 1 << 6;
    pub const GPU_TIME: u16 = 1 << 7;
    pub const GPU_MEMORY: u16 = 1 << 8;
    pub const GPU_FLOPS: u16 = 1 << 9;
    /// Mirrors `Datadog::SampleType::All` (`types.hpp`) -- the mask
    /// `ProfilerState` actually uses in production: `ddup_config_sample_type`
    /// is declared but never called anywhere in `_ddup.pyx`, so
    /// `profiler_state.hpp`'s `type_mask{SampleType::All}` default is the
    /// mask every real profile is built with. Callers other than tests
    /// should use this, not an arbitrary subset.
    pub const ALL: u16 = CPU
        | WALL
        | EXCEPTION
        | LOCK_ACQUIRE
        | LOCK_RELEASE
        | ALLOCATION
        | HEAP
        | GPU_TIME
        | GPU_MEMORY
        | GPU_FLOPS;
}

/// Mirrors `Datadog::ValueIndex` (`types.hpp`) -- offsets into a sample's flat
/// `values` vector for each configured sample type. `None` means that sample
/// type isn't configured for this profiler instance.
#[derive(Default, Clone, Copy)]
struct ValueIndex {
    cpu_time: Option<usize>,
    cpu_count: Option<usize>,
    wall_time: Option<usize>,
    wall_count: Option<usize>,
    exception_count: Option<usize>,
    lock_acquire_time: Option<usize>,
    lock_acquire_count: Option<usize>,
    lock_release_time: Option<usize>,
    lock_release_count: Option<usize>,
    alloc_space: Option<usize>,
    alloc_count: Option<usize>,
    heap_space: Option<usize>,
    heap_count: Option<usize>,
    gpu_time: Option<usize>,
    gpu_count: Option<usize>,
    gpu_alloc_space: Option<usize>,
    gpu_alloc_count: Option<usize>,
    gpu_flops: Option<usize>,
    gpu_flops_samples: Option<usize>,
}

/// Builds `(sample_types, ValueIndex)` from a `SampleTypeMask`, mirroring
/// `Datadog::Profile::setup_samplers()` (`profile.cpp`) exactly, including
/// enable-order (which determines the pprof sample-type column order).
fn setup_samplers(type_mask: u16) -> (Vec<api::SampleType>, ValueIndex) {
    let mut sample_types = Vec::new();
    let mut val_idx = ValueIndex::default();
    let add = |sample_types: &mut Vec<api::SampleType>, st: api::SampleType| -> usize {
        let idx = sample_types.len();
        sample_types.push(st);
        idx
    };

    if type_mask & SampleTypeMask::CPU != 0 {
        val_idx.cpu_time = Some(add(&mut sample_types, api::SampleType::CpuTime));
        val_idx.cpu_count = Some(add(&mut sample_types, api::SampleType::CpuSamples));
    }
    if type_mask & SampleTypeMask::WALL != 0 {
        val_idx.wall_time = Some(add(&mut sample_types, api::SampleType::WallTime));
        val_idx.wall_count = Some(add(&mut sample_types, api::SampleType::WallSamples));
    }
    if type_mask & SampleTypeMask::EXCEPTION != 0 {
        val_idx.exception_count = Some(add(&mut sample_types, api::SampleType::ExceptionSamples));
    }
    if type_mask & SampleTypeMask::LOCK_ACQUIRE != 0 {
        val_idx.lock_acquire_time = Some(add(&mut sample_types, api::SampleType::LockAcquireWait));
        val_idx.lock_acquire_count = Some(add(&mut sample_types, api::SampleType::LockAcquire));
    }
    if type_mask & SampleTypeMask::LOCK_RELEASE != 0 {
        val_idx.lock_release_time = Some(add(&mut sample_types, api::SampleType::LockReleaseHold));
        val_idx.lock_release_count = Some(add(&mut sample_types, api::SampleType::LockRelease));
    }
    if type_mask & SampleTypeMask::ALLOCATION != 0 {
        val_idx.alloc_space = Some(add(&mut sample_types, api::SampleType::AllocSpace));
        val_idx.alloc_count = Some(add(&mut sample_types, api::SampleType::AllocSamples));
    }
    if type_mask & SampleTypeMask::HEAP != 0 {
        val_idx.heap_space = Some(add(&mut sample_types, api::SampleType::HeapSpace));
        val_idx.heap_count = Some(add(&mut sample_types, api::SampleType::HeapLiveSamples));
    }
    if type_mask & SampleTypeMask::GPU_TIME != 0 {
        val_idx.gpu_time = Some(add(&mut sample_types, api::SampleType::GpuTime));
        val_idx.gpu_count = Some(add(&mut sample_types, api::SampleType::GpuSamples));
    }
    if type_mask & SampleTypeMask::GPU_MEMORY != 0 {
        val_idx.gpu_alloc_space = Some(add(&mut sample_types, api::SampleType::GpuSpace));
        val_idx.gpu_alloc_count = Some(add(&mut sample_types, api::SampleType::GpuAllocSamples));
    }
    if type_mask & SampleTypeMask::GPU_FLOPS != 0 {
        val_idx.gpu_flops = Some(add(&mut sample_types, api::SampleType::GpuFlops));
        val_idx.gpu_flops_samples = Some(add(&mut sample_types, api::SampleType::GpuFlopsSamples));
    }

    (sample_types, val_idx)
}

/// Mirrors `Datadog::ExportLabelKey` (`libdatadog_helpers.hpp`) -- the closed
/// set of label keys the profiler emits, pre-interned once at
/// `DdProfilerState::new()` so per-push label calls never touch the
/// dictionary for the key, only (optionally) for frame strings.
#[derive(Clone, Copy)]
#[allow(dead_code)]
struct LabelKeys {
    exception_type: StringId2,
    exception_message: StringId2,
    thread_id: StringId2,
    thread_native_id: StringId2,
    thread_name: StringId2,
    task_id: StringId2,
    task_name: StringId2,
    span_id: StringId2,
    local_root_span_id: StringId2,
    trace_type: StringId2,
    class_name: StringId2,
    lock_name: StringId2,
    gpu_device_name: StringId2,
}

impl LabelKeys {
    fn try_new(dictionary: &ProfilesDictionary) -> anyhow::Result<Self> {
        Ok(Self {
            exception_type: dictionary.try_insert_str2("exception type")?,
            exception_message: dictionary.try_insert_str2("exception message")?,
            thread_id: dictionary.try_insert_str2("thread id")?,
            thread_native_id: dictionary.try_insert_str2("thread native id")?,
            thread_name: dictionary.try_insert_str2("thread name")?,
            task_id: dictionary.try_insert_str2("task id")?,
            task_name: dictionary.try_insert_str2("task name")?,
            span_id: dictionary.try_insert_str2("span id")?,
            local_root_span_id: dictionary.try_insert_str2("local root span id")?,
            trace_type: dictionary.try_insert_str2("trace type")?,
            class_name: dictionary.try_insert_str2("class name")?,
            lock_name: dictionary.try_insert_str2("lock name")?,
            gpu_device_name: dictionary.try_insert_str2("gpu device name")?,
        })
    }
}

/// An owned label value pushed onto a `SampleHandle`, mirroring the two
/// `Sample::push_label` overloads in `sample.cpp` (string- and int-valued).
/// Values are kept owned (not pre-interned) exactly like `sample.cpp`'s
/// `StringArena`-backed labels: label *values* (thread names, task names,
/// lock names, ...) are often high-cardinality/short-lived, so interning
/// them into the shared dictionary would leak memory -- only label *keys*
/// and frame strings are interned.
enum LabelValue {
    Str(String),
    Num(i64),
}

struct OwnedLabel {
    key: StringId2,
    value: LabelValue,
}

/// Mirrors `Datadog::Sample` (`sample.hpp`): a single profiling sample being
/// built, one Python call per push -- see de-risking fact 1 in
/// `OPTION_C_ONE_HOP_PLAN.md`. Not `Send`: like the existing Cython
/// `SampleHandle`, a handle is created, pushed to, and flushed from a single
/// thread while the GIL is held, so there is no need to pay for a `Send`
/// bound (which the `FunctionId2`/`MappingId2` dictionary handles below
/// don't have anyway -- see plan Stage 0 grounding notes).
#[pyclass(name = "SampleHandle", module = "ddtrace.internal._native", unsendable)]
pub struct SampleHandlePy {
    dictionary: DictArc<ProfilesDictionary>,
    label_keys: LabelKeys,
    max_nframes: usize,

    locations: Vec<Location2>,
    dropped_frames: u64,
    labels: Vec<OwnedLabel>,
    values: Vec<i64>,
    val_idx: ValueIndex,
    timestamp_ns: Option<i64>,
    // Mirrors the Cython `SampleHandle`'s `self.ptr = NULL` after
    // `flush_sample()`: a handle can only be added to a profile once. Without
    // this, `add_sample` would silently double-count a sample flushed twice.
    flushed: std::cell::Cell<bool>,
}

impl SampleHandlePy {
    fn new(
        dictionary: DictArc<ProfilesDictionary>,
        label_keys: LabelKeys,
        max_nframes: usize,
        val_idx: ValueIndex,
        num_values: usize,
    ) -> Self {
        Self {
            dictionary,
            label_keys,
            max_nframes,
            locations: Vec::with_capacity(max_nframes + 1),
            dropped_frames: 0,
            labels: Vec::new(),
            values: vec![0; num_values],
            val_idx,
            timestamp_ns: None,
            flushed: std::cell::Cell::new(false),
        }
    }

    fn push_value(&mut self, idx: Option<usize>, val: i64) {
        if let Some(idx) = idx {
            self.values[idx] = val;
        }
    }

    fn push_label_str(&mut self, key: StringId2, val: &str) {
        if val.is_empty() {
            return;
        }
        self.labels.push(OwnedLabel {
            key,
            value: LabelValue::Str(val.to_owned()),
        });
    }

    fn push_label_num(&mut self, key: StringId2, val: i64) {
        self.labels.push(OwnedLabel {
            key,
            value: LabelValue::Num(val),
        });
    }
}

#[pymethods]
impl SampleHandlePy {
    fn push_cputime(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.cpu_time, value);
        self.push_value(self.val_idx.cpu_count, count);
    }

    fn push_walltime(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.wall_time, value);
        self.push_value(self.val_idx.wall_count, count);
    }

    fn push_acquire(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.lock_acquire_time, value);
        self.push_value(self.val_idx.lock_acquire_count, count);
    }

    fn push_release(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.lock_release_time, value);
        self.push_value(self.val_idx.lock_release_count, count);
    }

    fn push_alloc(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.alloc_space, value);
        self.push_value(self.val_idx.alloc_count, count);
    }

    fn push_heap(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.heap_space, value);
        self.push_value(self.val_idx.heap_count, count);
    }

    fn push_gpu_gputime(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.gpu_time, value);
        self.push_value(self.val_idx.gpu_count, count);
    }

    fn push_gpu_memory(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.gpu_alloc_space, value);
        self.push_value(self.val_idx.gpu_alloc_count, count);
    }

    fn push_gpu_flops(&mut self, value: i64, count: i64) {
        self.push_value(self.val_idx.gpu_flops, value);
        self.push_value(self.val_idx.gpu_flops_samples, count);
    }

    fn push_lock_name(&mut self, lock_name: &str) {
        let key = self.label_keys.lock_name;
        self.push_label_str(key, lock_name);
    }

    /// Interns `name`/`filename` into the shared `ProfilesDictionary` and
    /// appends a `Location2`, mirroring `Sample::push_frame_impl`
    /// (`sample.cpp`). Frames beyond `max_nframes` are dropped and counted,
    /// matching `Sample::push_frame`'s truncation behavior.
    fn push_frame(&mut self, name: &str, filename: &str, address: u64, line: i64) -> PyResult<()> {
        if self.locations.len() >= self.max_nframes {
            self.dropped_frames += 1;
            return Ok(());
        }
        let name_id = self
            .dictionary
            .try_insert_str2(name)
            .map_err(|e| PyValueError::new_err(format!("failed to intern frame name: {e:?}")))?;
        let filename_id = self.dictionary.try_insert_str2(filename).map_err(|e| {
            PyValueError::new_err(format!("failed to intern frame filename: {e:?}"))
        })?;
        // system_name has no Python equivalent -- mirrors
        // `intern_function`'s use of `state.cached_empty_string_id`.
        let empty_id = self
            .dictionary
            .try_insert_str2("")
            .map_err(|e| PyValueError::new_err(format!("failed to intern empty string: {e:?}")))?;
        let function_id = self
            .dictionary
            .try_insert_function2(libdd_profiling::profiles::datatypes::Function2 {
                name: name_id,
                system_name: empty_id,
                file_name: filename_id,
            })
            .map_err(|e| {
                PyValueError::new_err(format!("failed to intern frame function: {e:?}"))
            })?;
        self.locations.push(Location2 {
            mapping: MappingId2::default(), // No support for mappings in Python.
            function: function_id,
            address,
            line,
        });
        Ok(())
    }

    fn incr_dropped_frames(&mut self, count: Option<u64>) {
        self.dropped_frames += count.unwrap_or(1);
    }

    fn push_threadinfo(&mut self, thread_id: i64, thread_native_id: i64, thread_name: &str) {
        let keys = self.label_keys;
        self.push_label_num(keys.thread_id, thread_id);
        self.push_label_num(keys.thread_native_id, thread_native_id);
        self.push_label_str(keys.thread_name, thread_name);
    }

    fn push_task_id(&mut self, task_id: u64) {
        let key = self.label_keys.task_id;
        self.push_label_num(key, task_id as i64);
    }

    fn push_task_name(&mut self, task_name: &str) {
        let key = self.label_keys.task_name;
        self.push_label_str(key, task_name);
    }

    fn push_span_id(&mut self, span_id: u64) {
        let key = self.label_keys.span_id;
        self.push_label_num(key, span_id as i64);
    }

    fn push_local_root_span_id(&mut self, local_root_span_id: u64) {
        let key = self.label_keys.local_root_span_id;
        self.push_label_num(key, local_root_span_id as i64);
    }

    fn push_trace_type(&mut self, trace_type: &str) {
        let key = self.label_keys.trace_type;
        self.push_label_str(key, trace_type);
    }

    fn push_exceptioninfo(&mut self, exception_type: &str, count: u64) {
        let key = self.label_keys.exception_type;
        self.push_label_str(key, exception_type);
        self.push_value(self.val_idx.exception_count, count as i64);
    }

    fn push_exception_message(&mut self, exception_message: &str) {
        let key = self.label_keys.exception_message;
        self.push_label_str(key, exception_message);
    }

    fn push_class_name(&mut self, class_name: &str) {
        let key = self.label_keys.class_name;
        self.push_label_str(key, class_name);
    }

    fn push_gpu_device_name(&mut self, device_name: &str) {
        let key = self.label_keys.gpu_device_name;
        self.push_label_str(key, device_name);
    }

    /// Mirrors `Sample::push_monotonic_ns` (`sample.cpp`): converts a
    /// monotonic timestamp to wall-clock epoch nanoseconds via a
    /// once-computed static offset, matching the C++ side exactly. A no-op
    /// when timeline isn't enabled, or when `monotonic_ns` is zero.
    fn push_monotonic_ns(&mut self, monotonic_ns: i64) -> bool {
        if monotonic_ns == 0 {
            return false;
        }
        if is_timeline_enabled() {
            static OFFSET: std::sync::OnceLock<i64> = std::sync::OnceLock::new();
            let offset = *OFFSET.get_or_init(|| {
                let epoch_ns = SystemTime::now()
                    .duration_since(SystemTime::UNIX_EPOCH)
                    .expect("system clock before Unix epoch")
                    .as_nanos() as i64;
                epoch_ns - get_monotonic_ns()
            });
            self.timestamp_ns = Some(monotonic_ns + offset);
        }
        true
    }

    /// Mirrors `Sample::push_absolute_ns` (`sample.cpp`): a no-op unless
    /// timeline is enabled.
    fn push_absolute_ns(&mut self, timestamp_ns: i64) {
        if is_timeline_enabled() {
            self.timestamp_ns = Some(timestamp_ns);
        }
    }
}

/// Mirrors `Datadog::ProfilerState` (`profiler_state.hpp`)'s profile-owning
/// slice: the `Profile`, its `ProfilesDictionary`, and sample-type
/// configuration. Fork-safety orchestration (`prefork`/`postfork_*`, upload
/// cancellation) is out of scope for this module -- see plan Stage 3.
#[pyclass(name = "DdProfile", module = "ddtrace.internal._native")]
pub struct DdProfilePy {
    profile: Mutex<Profile>,
    dictionary: DictArc<ProfilesDictionary>,
    label_keys: LabelKeys,
    max_nframes: usize,
    val_idx: ValueIndex,
    num_values: usize,
    // Only used for exposing the current configured max/label state; kept as
    // an atomic for cheap concurrent reads without touching the profile
    // mutex (mirrors `type_mask`/`max_nframes` being plain fields read
    // without locking in `sample.cpp`).
    type_mask: AtomicU16,
}

#[pymethods]
impl DdProfilePy {
    #[new]
    fn new(type_mask: u16, max_nframes: u32) -> PyResult<Self> {
        let dictionary = DictArc::try_new(ProfilesDictionary::try_new().map_err(|e| {
            PyValueError::new_err(format!("failed to create profiles dictionary: {e:?}"))
        })?)
        .map_err(|_| PyValueError::new_err("failed to allocate profiles dictionary"))?;
        let label_keys = LabelKeys::try_new(&dictionary).map_err(to_py_err)?;

        let (sample_types, val_idx) = setup_samplers(type_mask);
        let num_values = sample_types.len();
        // Whatever the first sampler happens to be is the default "period"
        // for the profile, mirroring `Profile::setup_samplers()`. The value
        // of 1 is a pointless default, matching dd_wrapper.
        let period = sample_types.first().map(|&sample_type| api::Period {
            sample_type,
            value: 1,
        });

        let profile_dictionary = dictionary
            .try_clone()
            .map_err(|_| PyValueError::new_err("failed to share profiles dictionary"))?;
        let profile = Profile::try_new_with_dictionary(&sample_types, period, profile_dictionary)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;

        Ok(Self {
            profile: Mutex::new(profile),
            dictionary,
            label_keys,
            max_nframes: max_nframes as usize,
            val_idx,
            num_values,
            type_mask: AtomicU16::new(type_mask),
        })
    }

    /// Mirrors `SampleManager::start_sample()` / `SampleHandle.__cinit__` --
    /// allocates a new, empty sample bound to this profile's dictionary.
    fn start_sample(&self) -> PyResult<SampleHandlePy> {
        let dictionary = self
            .dictionary
            .try_clone()
            .map_err(|_| PyValueError::new_err("failed to share profiles dictionary"))?;
        Ok(SampleHandlePy::new(
            dictionary,
            self.label_keys,
            self.max_nframes,
            self.val_idx,
            self.num_values,
        ))
    }

    /// Mirrors `Sample::export_sample()`/`flush_sample()`: adds the sample's
    /// locations/labels/values to the profile. Takes the profile mutex only
    /// here, not per push -- see module docs.
    ///
    /// # Safety (internal)
    /// `try_add_sample2` requires every `Location2`/`FunctionId2`/`MappingId2`
    /// handle passed in to have come from the same `ProfilesDictionary` this
    /// profile was constructed with (`try_new_with_dictionary`). This holds
    /// here because `handle.dictionary` is always a `try_clone()` of
    /// `self.dictionary` -- the same underlying dictionary, obtained via
    /// `start_sample()`.
    fn add_sample(&self, handle: &SampleHandlePy) -> PyResult<()> {
        if handle.flushed.replace(true) {
            return Err(PyValueError::new_err(
                "sample handle has already been added to a profile",
            ));
        }
        // Mirrors `Sample::export_sample()`: frames beyond `max_nframes` are
        // dropped by `push_frame`, but their count is still surfaced as one
        // extra synthetic `<N frame(s) omitted>` location, added here (not
        // subject to the `max_nframes` cap itself).
        let mut locations = handle.locations.clone();
        if handle.dropped_frames > 0 {
            let name = format!(
                "<{} frame{} omitted>",
                handle.dropped_frames,
                if handle.dropped_frames == 1 { "" } else { "s" }
            );
            let name_id = handle.dictionary.try_insert_str2(&name).map_err(|e| {
                PyValueError::new_err(format!("failed to intern frame name: {e:?}"))
            })?;
            let empty_id = handle.dictionary.try_insert_str2("").map_err(|e| {
                PyValueError::new_err(format!("failed to intern empty string: {e:?}"))
            })?;
            let function_id = handle
                .dictionary
                .try_insert_function2(libdd_profiling::profiles::datatypes::Function2 {
                    name: name_id,
                    system_name: empty_id,
                    file_name: empty_id,
                })
                .map_err(|e| {
                    PyValueError::new_err(format!("failed to intern frame function: {e:?}"))
                })?;
            locations.push(Location2 {
                mapping: MappingId2::default(),
                function: function_id,
                address: 0,
                line: 0,
            });
        }
        let labels = handle.labels.iter().map(|label| {
            Ok::<_, anyhow::Error>(match &label.value {
                LabelValue::Str(s) => Label2::str(label.key, s.as_str()),
                LabelValue::Num(n) => Label2::num(label.key, *n, ""),
            })
        });
        let timestamp = handle
            .timestamp_ns
            .map(|ns| {
                std::num::NonZeroI64::new(ns)
                    .ok_or_else(|| PyValueError::new_err("timestamp must be non-zero"))
            })
            .transpose()?;

        let mut profile = self.profile.lock();
        // Safety: see doc comment above.
        unsafe {
            profile
                .try_add_sample2(&locations, &handle.values, labels, timestamp)
                .map_err(to_py_err)?;
        }
        Ok(())
    }

    fn add_endpoint(&self, local_root_span_id: u64, endpoint: &str) -> PyResult<()> {
        let mut profile = self.profile.lock();
        profile
            .add_endpoint(local_root_span_id, endpoint.into())
            .map_err(to_py_err)
    }

    fn add_endpoint_count(&self, endpoint: &str, value: i64) -> PyResult<()> {
        let mut profile = self.profile.lock();
        profile
            .add_endpoint_count(endpoint.into(), value)
            .map_err(to_py_err)
    }

    /// Mirrors dd_wrapper's rotate-then-serialize cycle (`Profile::reset_profile`
    /// + `ddup_serialize`), but using the safe Rust API's swap-based rotation
    /// (`reset_and_return_previous`) instead of the C ABI's in-place reset:
    /// swaps in a fresh `Profile` sharing this instance's dictionary/sample
    /// types, then serializes and compresses the one being replaced.
    ///
    /// Returns `(pprof_bytes, start_ns, end_ns)`. `end_time_ns` defaults to
    /// "now" when omitted, matching `serialize_into_compressed_pprof`.
    fn serialize(&self, end_time_ns: Option<i64>) -> PyResult<(Vec<u8>, i64, i64)> {
        let previous = {
            let mut profile = self.profile.lock();
            profile.reset_and_return_previous().map_err(to_py_err)?
        };
        let end_time = end_time_ns.map(ns_to_systemtime).transpose()?;
        let encoded = previous
            .serialize_into_compressed_pprof(end_time, None)
            .map_err(to_py_err)?;
        let start_ns = encoded
            .start
            .duration_since(SystemTime::UNIX_EPOCH)
            .map_err(|e| PyValueError::new_err(e.to_string()))?
            .as_nanos() as i64;
        let end_ns = encoded
            .end
            .duration_since(SystemTime::UNIX_EPOCH)
            .map_err(|e| PyValueError::new_err(e.to_string()))?
            .as_nanos() as i64;
        Ok((encoded.buffer, start_ns, end_ns))
    }

    fn max_nframes(&self) -> usize {
        self.max_nframes
    }

    fn type_mask(&self) -> u16 {
        self.type_mask.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setup_samplers_matches_type_mask_order() {
        let (sample_types, val_idx) =
            setup_samplers(SampleTypeMask::CPU | SampleTypeMask::WALL | SampleTypeMask::EXCEPTION);
        assert_eq!(
            sample_types,
            vec![
                api::SampleType::CpuTime,
                api::SampleType::CpuSamples,
                api::SampleType::WallTime,
                api::SampleType::WallSamples,
                api::SampleType::ExceptionSamples,
            ]
        );
        assert_eq!(val_idx.cpu_time, Some(0));
        assert_eq!(val_idx.cpu_count, Some(1));
        assert_eq!(val_idx.wall_time, Some(2));
        assert_eq!(val_idx.wall_count, Some(3));
        assert_eq!(val_idx.exception_count, Some(4));
        assert_eq!(val_idx.lock_acquire_time, None);
    }

    fn new_profile(type_mask: u16, max_nframes: u32) -> DdProfilePy {
        DdProfilePy::new(type_mask, max_nframes).expect("profile construction failed")
    }

    #[test]
    fn push_frame_interns_and_appends_location() {
        let profile = new_profile(SampleTypeMask::WALL, 64);
        let mut handle = profile.start_sample().expect("start_sample failed");

        handle
            .push_frame("my_function", "my_module.py", 0, 42)
            .expect("push_frame failed");
        handle
            .push_frame("caller", "my_module.py", 0, 10)
            .expect("push_frame failed");

        assert_eq!(handle.locations.len(), 2);
        assert_eq!(handle.dropped_frames, 0);
    }

    #[test]
    fn push_frame_drops_beyond_max_nframes() {
        let profile = new_profile(SampleTypeMask::WALL, 1);
        let mut handle = profile.start_sample().expect("start_sample failed");

        handle
            .push_frame("f1", "a.py", 0, 1)
            .expect("push_frame failed");
        handle
            .push_frame("f2", "a.py", 0, 2)
            .expect("push_frame failed");

        assert_eq!(handle.locations.len(), 1);
        assert_eq!(handle.dropped_frames, 1);
    }

    #[test]
    fn add_sample_round_trips_through_profile() {
        let profile = new_profile(SampleTypeMask::WALL, 64);
        let mut handle = profile.start_sample().expect("start_sample failed");

        handle
            .push_frame("hot_loop", "app.py", 0, 100)
            .expect("push_frame failed");
        handle.push_walltime(1_000_000, 1);
        handle.push_threadinfo(1, 100, "MainThread");
        handle.push_task_name("worker-task");

        profile.add_sample(&handle).expect("add_sample failed");
    }

    #[test]
    fn add_sample_rejects_double_flush() {
        // Constructing the `PyErr` on the failure path below touches Python
        // type objects, unlike the other tests here which only exercise the
        // success path.
        pyo3::Python::initialize();
        let profile = new_profile(SampleTypeMask::WALL, 64);
        let handle = profile.start_sample().expect("start_sample failed");

        profile
            .add_sample(&handle)
            .expect("first add_sample failed");
        let err = profile
            .add_sample(&handle)
            .expect_err("second add_sample should be rejected");
        assert!(err.to_string().contains("already been added"));
    }

    #[test]
    fn serialize_rotates_and_produces_nonempty_pprof() {
        let profile = new_profile(SampleTypeMask::WALL, 64);
        let mut handle = profile.start_sample().expect("start_sample failed");
        handle
            .push_frame("hot_loop", "app.py", 0, 100)
            .expect("push_frame failed");
        handle.push_walltime(1_000_000, 1);
        profile.add_sample(&handle).expect("add_sample failed");

        let (buffer, start_ns, end_ns) = profile.serialize(None).expect("serialize failed");
        assert!(!buffer.is_empty());
        assert!(end_ns >= start_ns);

        // The profile was rotated, so a fresh sample can still be added.
        let handle2 = profile.start_sample().expect("start_sample failed");
        profile
            .add_sample(&handle2)
            .expect("add_sample after rotation failed");
    }

    #[test]
    fn push_monotonic_ns_is_noop_without_timeline() {
        let profile = new_profile(SampleTypeMask::WALL, 64);
        let mut handle = profile.start_sample().expect("start_sample failed");
        handle.push_monotonic_ns(123_456_789);
        assert_eq!(handle.timestamp_ns, None);
    }

    #[test]
    fn label_values_are_owned_not_interned() {
        // Regression guard: label *values* must stay owned Strings (never
        // pre-interned into the shared dictionary), matching sample.cpp's
        // StringArena rationale -- interning high-cardinality/short-lived
        // values like task names would leak dictionary memory for the
        // lifetime of the process.
        let profile = new_profile(SampleTypeMask::WALL, 64);
        let mut handle = profile.start_sample().expect("start_sample failed");
        handle.push_task_name("short-lived-task-name");
        assert!(matches!(
            handle.labels.last().unwrap().value,
            LabelValue::Str(_)
        ));
    }
}
