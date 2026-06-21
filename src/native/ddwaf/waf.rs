//! Builder / handle / context / subcontext bindings, mirroring the `py_ddwaf_*` helpers and the
//! `ddwaf_*` lifecycle/eval prototypes that the ctypes layer declared.
//!
//! Long-running calls (build, config update, eval) release the GIL via `Python::detach`. Raw
//! libddwaf pointers are not `Send`, so we pass them through the detached closure as `usize`
//! addresses. Concurrent eval on the *same* context is still serialized by the Python-side capsule
//! lock; distinct contexts may run concurrently (each with its own wrapper).

use std::ffi::{c_char, CStr};

use libddwaf_sys as sys;
use pyo3::prelude::*;

use crate::ddwaf::object::{DDWafAllocator, DDWafObject};

macro_rules! ptr_wrapper {
    ($name:ident, $raw:ty, $field:ident, $destroy:path) => {
        #[pyclass(module = "ddtrace.internal.native._native.ddwaf")]
        pub struct $name {
            pub(crate) $field: $raw,
        }
        // SAFETY: access is serialized by the GIL and the Python-side capsule locks. libddwaf
        // handles/contexts are effectively immutable or independently owned.
        unsafe impl Send for $name {}
        unsafe impl Sync for $name {}
        #[pymethods]
        impl $name {
            fn __bool__(&self) -> bool {
                !self.$field.is_null()
            }
        }
        impl Drop for $name {
            fn drop(&mut self) {
                if !self.$field.is_null() {
                    unsafe { $destroy(self.$field) };
                }
            }
        }
    };
}

ptr_wrapper!(
    DDWafBuilder,
    sys::ddwaf_builder,
    raw,
    sys::ddwaf_builder_destroy
);
ptr_wrapper!(DDWafHandle, sys::ddwaf_handle, raw, sys::ddwaf_destroy);
ptr_wrapper!(
    DDWafContext,
    sys::ddwaf_context,
    raw,
    sys::ddwaf_context_destroy
);
ptr_wrapper!(
    DDWafSubcontext,
    sys::ddwaf_subcontext,
    raw,
    sys::ddwaf_subcontext_destroy
);

#[pyfunction]
pub fn ddwaf_get_version() -> &'static CStr {
    libddwaf::version()
}

#[pyfunction]
pub fn ddwaf_builder_init() -> DDWafBuilder {
    DDWafBuilder {
        raw: unsafe { sys::ddwaf_builder_init() },
    }
}

#[pyfunction]
pub fn ddwaf_builder_add_or_update_config(
    py: Python<'_>,
    builder: &DDWafBuilder,
    path: &[u8],
    path_len: u32,
    config: &DDWafObject,
    diagnostics: &DDWafObject,
) -> bool {
    let builder = builder.raw as usize;
    let config = config.ptr as usize;
    let diagnostics = diagnostics.ptr as usize;
    let path = path.to_vec();
    py.detach(move || unsafe {
        sys::ddwaf_builder_add_or_update_config(
            builder as sys::ddwaf_builder,
            path.as_ptr() as *const c_char,
            path_len,
            config as *const sys::ddwaf_object,
            diagnostics as *mut sys::ddwaf_object,
        )
    })
}

#[pyfunction]
pub fn ddwaf_builder_remove_config(builder: &DDWafBuilder, path: &[u8], path_len: u32) -> bool {
    unsafe {
        sys::ddwaf_builder_remove_config(builder.raw, path.as_ptr() as *const c_char, path_len)
    }
}

#[pyfunction]
pub fn ddwaf_builder_build_instance(py: Python<'_>, builder: &DDWafBuilder) -> DDWafHandle {
    let builder = builder.raw as usize;
    // Return the handle as a `usize` from the detached closure: raw libddwaf pointers are not
    // `Send`/`Ungil` and cannot be carried back out of `detach` directly.
    let raw = py.detach(move || unsafe {
        sys::ddwaf_builder_build_instance(builder as sys::ddwaf_builder)
    } as usize);
    DDWafHandle {
        raw: raw as sys::ddwaf_handle,
    }
}

#[pyfunction]
pub fn ddwaf_builder_get_config_paths(
    builder: &DDWafBuilder,
    filter_re: &[u8],
    filter_len: u32,
) -> u32 {
    unsafe {
        sys::ddwaf_builder_get_config_paths(
            builder.raw,
            std::ptr::null_mut(),
            filter_re.as_ptr() as *const c_char,
            filter_len,
        )
    }
}

#[pyfunction]
pub fn ddwaf_known_addresses(handle: &DDWafHandle) -> Vec<String> {
    let mut size: u32 = 0;
    let ptr = unsafe { sys::ddwaf_known_addresses(handle.raw, &mut size) };
    if ptr.is_null() {
        return Vec::new();
    }
    (0..size as usize)
        .map(|i| {
            let s = unsafe { *ptr.add(i) };
            unsafe { CStr::from_ptr(s) }.to_string_lossy().into_owned()
        })
        .collect()
}

#[pyfunction]
pub fn ddwaf_context_init(handle: &DDWafHandle, alloc: &DDWafAllocator) -> DDWafContext {
    DDWafContext {
        raw: unsafe { sys::ddwaf_context_init(handle.raw, alloc.raw) },
    }
}

#[pyfunction]
pub fn ddwaf_subcontext_init(context: &DDWafContext) -> DDWafSubcontext {
    DDWafSubcontext {
        raw: unsafe { sys::ddwaf_subcontext_init(context.raw) },
    }
}

#[pyfunction]
pub fn ddwaf_context_eval(
    py: Python<'_>,
    context: &DDWafContext,
    data: &DDWafObject,
    alloc: &DDWafAllocator,
    result: &DDWafObject,
    timeout_us: u64,
) -> i32 {
    let context = context.raw as usize;
    let data = data.ptr as usize;
    let alloc = alloc.raw as usize;
    let result = result.ptr as usize;
    py.detach(move || unsafe {
        sys::ddwaf_context_eval(
            context as sys::ddwaf_context,
            data as *mut sys::ddwaf_object,
            alloc as sys::ddwaf_allocator,
            result as *mut sys::ddwaf_object,
            timeout_us,
        )
    })
}

#[pyfunction]
pub fn ddwaf_subcontext_eval(
    py: Python<'_>,
    subcontext: &DDWafSubcontext,
    data: &DDWafObject,
    alloc: &DDWafAllocator,
    result: &DDWafObject,
    timeout_us: u64,
) -> i32 {
    let subcontext = subcontext.raw as usize;
    let data = data.ptr as usize;
    let alloc = alloc.raw as usize;
    let result = result.ptr as usize;
    py.detach(move || unsafe {
        sys::ddwaf_subcontext_eval(
            subcontext as sys::ddwaf_subcontext,
            data as *mut sys::ddwaf_object,
            alloc as sys::ddwaf_allocator,
            result as *mut sys::ddwaf_object,
            timeout_us,
        )
    })
}

/// Registers the WAF lifecycle types and functions on the `ddwaf` submodule.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DDWafBuilder>()?;
    m.add_class::<DDWafHandle>()?;
    m.add_class::<DDWafContext>()?;
    m.add_class::<DDWafSubcontext>()?;
    m.add_function(wrap_pyfunction!(ddwaf_get_version, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_builder_init, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_builder_add_or_update_config, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_builder_remove_config, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_builder_build_instance, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_builder_get_config_paths, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_known_addresses, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_context_init, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_subcontext_init, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_context_eval, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_subcontext_eval, m)?)?;
    Ok(())
}
