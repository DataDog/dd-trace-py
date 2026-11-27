//! Read-only layout descriptor for the stack profiler.
//!
//! The stack profiler labels samples with the span that is active in each execution unit. To do
//! that without calling into Python and without the GIL, it reads `DD_CONTEXTVAR` out of the
//! unit's `contextvars.Context` and follows the tracer's objects in memory. This module
//! publishes where those objects keep the fields it needs, in a versioned `#[repr(C)]`
//! descriptor exported as the capsule `ddtrace.internal.native._native._profiler_layout`.
//! `profiler_layout.h` is the matching C header.
//!
//! Every field a reader uses is either set before the object is published and never changed
//! afterwards (span ids, parent pointers), or atomic (the finished flag, span type ids, the
//! Context span id mirror, `_reactivate` and the propagated profiler link). Readers never follow
//! a string pointer: span types are ids into `span_types`.
//!
//! The descriptor is filled once at module init and never changes, except `span_type`, which
//! `ddtrace._trace.span` sets once with a release store after defining `Span`.

pub mod span_types;

use std::ffi::c_void;
use std::ptr::NonNull;
use std::sync::atomic::{AtomicPtr, AtomicU32, Ordering};
use std::sync::OnceLock;

use pyo3::exceptions::{PyRuntimeError, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyCapsule, PyType};

use crate::context::Context;
use crate::span::SpanData;

/// Bump on any change to `ProfilerLayout` or to the meaning of a field it describes.
pub const VERSION: u32 = 1;

const CAPSULE_NAME: &std::ffi::CStr = c"ddtrace.internal.native._native._profiler_layout";

/// Offsets, in bytes from the start of a `SpanData` (or subclass) object.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct SpanOffsets {
    /// One copy of `copy_size` bytes from `copy_offset` holds every field below.
    pub copy_offset: u32,
    pub copy_size: u32,
    /// `uint64_t`, set before publication.
    pub span_id: u32,
    /// `uint64_t`, 0 when the span has no parent id. Set before publication.
    pub parent_id: u32,
    /// `PyObject*` to the parent span, NULL for none. Set before publication.
    pub parent: u32,
    /// `PyObject*` to the local root span, NULL when the span is its own local root.
    pub local_root: u32,
    /// `PyObject*` to the parent `Context`, NULL for none. Set before publication.
    pub parent_context: u32,
    /// Atomic `uint32_t` span type id.
    pub span_type_id: u32,
    /// Atomic `uint8_t`: 1 when the span has finished.
    pub finished: u32,
    pub _reserved: u32,
}

/// Offsets, in bytes from the start of a `Context` (or subclass) object.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct ContextOffsets {
    /// One copy of `copy_size` bytes from `copy_offset` holds every field below.
    pub copy_offset: u32,
    pub copy_size: u32,
    /// Atomic `uint64_t`: low 64 bits of `span_id`, 0 when `span_id` is None.
    pub span_id: u32,
    /// Atomic `uint64_t`: propagated local root span id, 0 when absent.
    pub local_root_span_id: u32,
    /// Atomic `uint32_t`: propagated span type id, 0 when absent.
    pub span_type_id: u32,
    /// Atomic `uint8_t`: `_reactivate`.
    pub reactivate: u32,
}

/// Where the span type table lives. See `span_types`.
#[repr(C)]
pub struct SpanTypeTable {
    /// `entries[id]` is valid for `id < *count`.
    pub entries: *const span_types::Entry,
    /// Atomic `uint32_t`, load with acquire ordering.
    pub count: *const AtomicU32,
    pub capacity: u32,
    /// Id stored when the table is full. Readers drop the span type label for it.
    pub unavailable_id: u32,
}

/// Mirrored as `dd_profiler_layout_t` in `profiler_layout.h`.
#[repr(C)]
pub struct ProfilerLayout {
    pub version: u32,
    /// `size_of::<ProfilerLayout>()`.
    pub size: u32,
    /// The `DD_CONTEXTVAR` `ContextVar` object. Readers compare HAMT keys to it by identity.
    pub contextvar: *const c_void,
    /// `hash(DD_CONTEXTVAR)`, a `Py_hash_t`.
    pub contextvar_hash: isize,
    /// The 32-bit key hash the HAMT uses for `DD_CONTEXTVAR` (`hamt_hash` in CPython's
    /// `Python/hamt.c`, unchanged from v3.9.0 to v3.16.0a2).
    pub contextvar_hamt_hash: u32,
    pub _reserved: u32,
    /// `ddtrace.internal.native._native.SpanData`.
    pub span_data_type: *const c_void,
    /// `ddtrace._trace.span.Span`, NULL until that module is imported. Load with acquire
    /// ordering.
    pub span_type: AtomicPtr<c_void>,
    /// `ddtrace._trace.context.Context`, the native `Context` type.
    pub context_type: *const c_void,
    pub span: SpanOffsets,
    pub context: ContextOffsets,
    pub span_types: SpanTypeTable,
}

// Must match profiler_layout.h on 64-bit platforms.
#[cfg(target_pointer_width = "64")]
const _: () = {
    use std::mem::offset_of;
    assert!(size_of::<ProfilerLayout>() == 144);
    assert!(offset_of!(ProfilerLayout, span_data_type) == 32);
    assert!(offset_of!(ProfilerLayout, span) == 56);
    assert!(offset_of!(ProfilerLayout, context) == 96);
    assert!(offset_of!(ProfilerLayout, span_types) == 120);
    assert!(size_of::<span_types::Entry>() == 16);
};

struct LayoutCell(ProfilerLayout);

// SAFETY: the raw pointers point to immortal objects (type objects, the context variable and
// the static span type table) and are never written after init; `span_type` is atomic.
unsafe impl Sync for LayoutCell {}
unsafe impl Send for LayoutCell {}

static LAYOUT: OnceLock<LayoutCell> = OnceLock::new();

/// Offset of `field` from the start of the Python object `base` that contains it.
pub(crate) fn field_offset<T>(base: *mut pyo3::ffi::PyObject, field: &T) -> u32 {
    let offset = (field as *const T as usize) - (base as usize);
    u32::try_from(offset).expect("field offset fits in u32")
}

/// The smallest byte range that holds a set of fields.
pub(crate) struct CopyRange {
    pub offset: u32,
    pub size: u32,
}

impl CopyRange {
    pub(crate) fn of(fields: &[(u32, usize)]) -> Self {
        let start = fields.iter().map(|(off, _)| *off).min().unwrap_or(0);
        let end = fields
            .iter()
            .map(|(off, size)| *off + *size as u32)
            .max()
            .unwrap_or(0);
        Self {
            offset: start,
            size: end - start,
        }
    }
}

/// The 32-bit key hash CPython's HAMT uses for an object with Python hash `hash`.
fn hamt_hash(hash: isize) -> u32 {
    #[cfg(target_pointer_width = "64")]
    {
        let hash = hash as i64;
        let xored = (hash as i32) ^ ((hash >> 32) as i32);
        (if xored == -1 { -2 } else { xored }) as u32
    }
    #[cfg(not(target_pointer_width = "64"))]
    {
        hash as i32 as u32
    }
}

fn build(py: Python<'_>) -> PyResult<ProfilerLayout> {
    span_types::init();
    let contextvar = crate::context_provider::contextvar(py)?;
    let contextvar_hash = contextvar.hash()?;
    Ok(ProfilerLayout {
        version: VERSION,
        size: size_of::<ProfilerLayout>() as u32,
        contextvar: contextvar.as_ptr() as *const c_void,
        contextvar_hash,
        contextvar_hamt_hash: hamt_hash(contextvar_hash),
        _reserved: 0,
        span_data_type: py.get_type::<SpanData>().as_ptr() as *const c_void,
        span_type: AtomicPtr::new(std::ptr::null_mut()),
        context_type: py.get_type::<Context>().as_ptr() as *const c_void,
        span: SpanData::profiler_offsets(py)?,
        context: Context::profiler_offsets(py)?,
        span_types: SpanTypeTable {
            entries: span_types::entries_ptr(),
            count: span_types::count_ptr(),
            capacity: span_types::CAPACITY as u32,
            unavailable_id: span_types::UNAVAILABLE_ID,
        },
    })
}

fn layout(py: Python<'_>) -> PyResult<&'static ProfilerLayout> {
    if let Some(cell) = LAYOUT.get() {
        return Ok(&cell.0);
    }
    let built = build(py)?;
    let _ = LAYOUT.set(LayoutCell(built));
    Ok(&LAYOUT.get().expect("layout initialized").0)
}

/// Record `ddtrace._trace.span.Span` in the descriptor, so readers can match production spans
/// by type without walking `tp_base`. The first call wins. Returns whether `cls` is now the
/// recorded type.
#[pyfunction]
fn _profiler_layout_set_span_type(py: Python<'_>, cls: &Bound<'_, PyType>) -> PyResult<bool> {
    if !cls.is_subclass_of::<SpanData>()? {
        return Err(PyTypeError::new_err("expected a subclass of SpanData"));
    }
    let layout = layout(py)?;
    let ptr = cls.as_ptr() as *mut c_void;
    match layout.span_type.compare_exchange(
        std::ptr::null_mut(),
        ptr,
        Ordering::Release,
        Ordering::Acquire,
    ) {
        Ok(_) => {
            // Keep the type alive for as long as readers may use the descriptor.
            std::mem::forget(cls.clone().unbind());
            Ok(true)
        }
        Err(current) => Ok(current == ptr),
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    let layout = layout(py)?;
    let pointer = NonNull::from(layout).cast::<c_void>();
    // SAFETY: the descriptor is a static that lives for the life of the process.
    let capsule = unsafe { PyCapsule::new_with_pointer(py, pointer, CAPSULE_NAME) }
        .map_err(|e| PyRuntimeError::new_err(format!("profiler layout capsule: {e}")))?;
    m.add("_profiler_layout", capsule)?;
    m.add_function(wrap_pyfunction!(_profiler_layout_set_span_type, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hamt_hash_matches_cpython() {
        // Values from CPython's hamt_hash: fold the halves, and map -1 to -2.
        assert_eq!(hamt_hash(0), 0);
        assert_eq!(hamt_hash(1), 1);
        assert_eq!(hamt_hash(-2), 0xffff_fffe ^ 0xffff_ffff);
        assert_eq!(hamt_hash(0x1234_5678_0000_0000), 0x1234_5678);
        assert_eq!(hamt_hash(0x0000_0000_ffff_ffff), 0xffff_fffe);
    }

    #[test]
    fn copy_range_covers_fields() {
        let r = CopyRange::of(&[(24, 8), (16, 8), (40, 1)]);
        assert_eq!((r.offset, r.size), (16, 25));
    }
}
