mod object;

use std::{ffi::c_char, ptr::NonNull};

use libddwaf_sys as sys;
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    pybacked::PyBackedBytes,
    pymodule, PyResult,
};

struct LibddwafPtr<T>(NonNull<T>);

impl<T> Copy for LibddwafPtr<T> {}

impl<T> Clone for LibddwafPtr<T> {
    fn clone(&self) -> Self {
        *self
    }
}

impl<T> LibddwafPtr<T> {
    fn new(raw: *mut T) -> Option<Self> {
        NonNull::new(raw).map(Self)
    }

    fn as_ptr(self) -> *mut T {
        self.0.as_ptr()
    }
}

// SAFETY: pointers never escape their owning Python wrapper, and every GIL-detached use is
// synchronous. Context and subcontext operations are additionally serialized below.
unsafe impl<T> Send for LibddwafPtr<T> {}
unsafe impl<T> Sync for LibddwafPtr<T> {}

fn byte_len(value: &PyBackedBytes) -> PyResult<u32> {
    value
        .len()
        .try_into()
        .map_err(|_| PyValueError::new_err("byte buffer is too large for libddwaf"))
}

fn bytes_address(value: &PyBackedBytes) -> *const c_char {
    value.as_ptr().cast()
}

fn default_allocator() -> PyResult<LibddwafPtr<sys::_ddwaf_allocator>> {
    LibddwafPtr::new(unsafe { sys::ddwaf_get_default_allocator() })
        .ok_or_else(|| PyRuntimeError::new_err("libddwaf did not provide its default allocator"))
}

#[pymodule(module = "appsec")]
pub mod libddwaf {
    use std::{
        ffi::CStr,
        sync::{atomic::Ordering, Arc, Mutex},
    };

    use libddwaf_sys as sys;
    use pyo3::{
        exceptions::PyRuntimeError, marker::Ungil, pybacked::PyBackedBytes, pyclass, pyfunction,
        PyResult, Python,
    };

    #[pymodule_export]
    use super::object::{
        ddwaf_object_from_json, ddwaf_object_insert, ddwaf_object_insert_key,
        ddwaf_object_set_array, ddwaf_object_set_bool, ddwaf_object_set_float,
        ddwaf_object_set_map, ddwaf_object_set_null, ddwaf_object_set_signed,
        ddwaf_object_set_string, DDWafObject,
    };
    use super::{byte_len, bytes_address, default_allocator, LibddwafPtr};

    #[pyclass(name = "DDWafBuilder", module = "appsec.libddwaf", frozen)]
    struct DDWafBuilder {
        raw: LibddwafPtr<sys::_ddwaf_builder>,
    }

    impl Drop for DDWafBuilder {
        fn drop(&mut self) {
            unsafe { sys::ddwaf_builder_destroy(self.raw.as_ptr()) };
        }
    }

    struct HandleInner {
        raw: LibddwafPtr<sys::_ddwaf_handle>,
    }

    impl Drop for HandleInner {
        fn drop(&mut self) {
            unsafe { sys::ddwaf_destroy(self.raw.as_ptr()) };
        }
    }

    #[pyclass(name = "DDWafHandle", module = "appsec.libddwaf", frozen)]
    struct DDWafHandle {
        inner: Arc<HandleInner>,
    }

    #[pyclass(name = "DDWafContext", module = "appsec.libddwaf")]
    struct DDWafContext {
        raw: LibddwafPtr<sys::_ddwaf_context>,
        operation: Mutex<()>,
        _handle: Arc<HandleInner>,
        #[pyo3(get, set)]
        rc_products: String,
    }

    impl Drop for DDWafContext {
        fn drop(&mut self) {
            unsafe { sys::ddwaf_context_destroy(self.raw.as_ptr()) };
        }
    }

    #[pyclass(name = "DDWafSubcontext", module = "appsec.libddwaf", frozen)]
    struct DDWafSubcontext {
        raw: LibddwafPtr<sys::_ddwaf_subcontext>,
        operation: Mutex<()>,
    }

    impl Drop for DDWafSubcontext {
        fn drop(&mut self) {
            unsafe { sys::ddwaf_subcontext_destroy(self.raw.as_ptr()) };
        }
    }

    // AIDEV-NOTE: Python builds object trees through short, GIL-held setter calls. Only JSON
    // parsing and evaluation detach the GIL. Context operations use one mutex. Eval transfers
    // object contents into libddwaf, while Rust continues to own only the stable top-level header.

    #[pyfunction]
    fn ddwaf_get_version() -> PyResult<String> {
        let raw = unsafe { sys::ddwaf_get_version() };
        if raw.is_null() {
            Ok(String::new())
        } else {
            unsafe { CStr::from_ptr(raw) }
                .to_str()
                .map(str::to_owned)
                .map_err(Into::into)
        }
    }

    #[pyfunction]
    fn ddwaf_known_addresses(handle: &DDWafHandle) -> PyResult<Vec<String>> {
        let mut size = 0;
        let addresses = unsafe { sys::ddwaf_known_addresses(handle.inner.raw.as_ptr(), &mut size) };
        if addresses.is_null() {
            return Ok(Vec::new());
        }
        (0..size as usize)
            .map(|index| {
                let address = unsafe { *addresses.add(index) };
                if address.is_null() {
                    Ok(String::new())
                } else {
                    unsafe { CStr::from_ptr(address) }
                        .to_str()
                        .map(str::to_owned)
                        .map_err(Into::into)
                }
            })
            .collect()
    }

    #[pyfunction]
    fn ddwaf_context_init(handle: &DDWafHandle) -> PyResult<Option<DDWafContext>> {
        let output_alloc = default_allocator()?;
        Ok(LibddwafPtr::new(unsafe {
            sys::ddwaf_context_init(handle.inner.raw.as_ptr(), output_alloc.as_ptr())
        })
        .map(|raw| DDWafContext {
            raw,
            operation: Mutex::new(()),
            _handle: Arc::clone(&handle.inner),
            rc_products: String::new(),
        }))
    }

    #[pyfunction]
    fn ddwaf_context_eval(
        py: Python<'_>,
        context: &DDWafContext,
        data: &DDWafObject,
        result: &DDWafObject,
        timeout: u64,
    ) -> PyResult<sys::DDWAF_RET_CODE> {
        eval(
            py,
            &context.operation,
            context.raw,
            data,
            result,
            timeout,
            |context, data, alloc, result, timeout| unsafe {
                sys::ddwaf_context_eval(context.as_ptr(), data, alloc, result, timeout)
            },
        )
    }

    #[pyfunction]
    fn ddwaf_subcontext_init(context: &DDWafContext) -> PyResult<Option<DDWafSubcontext>> {
        let _operation = context
            .operation
            .lock()
            .map_err(|_| PyRuntimeError::new_err("DDWafContext lock was poisoned"))?;
        Ok(
            LibddwafPtr::new(unsafe { sys::ddwaf_subcontext_init(context.raw.as_ptr()) }).map(
                |raw| DDWafSubcontext {
                    raw,
                    operation: Mutex::new(()),
                },
            ),
        )
    }

    #[pyfunction]
    fn ddwaf_subcontext_eval(
        py: Python<'_>,
        subcontext: &DDWafSubcontext,
        data: &DDWafObject,
        result: &DDWafObject,
        timeout: u64,
    ) -> PyResult<sys::DDWAF_RET_CODE> {
        eval(
            py,
            &subcontext.operation,
            subcontext.raw,
            data,
            result,
            timeout,
            |subcontext, data, alloc, result, timeout| unsafe {
                sys::ddwaf_subcontext_eval(subcontext.as_ptr(), data, alloc, result, timeout)
            },
        )
    }

    fn eval<R, F>(
        py: Python<'_>,
        operation: &Mutex<()>,
        context: LibddwafPtr<R>,
        data: &DDWafObject,
        result: &DDWafObject,
        timeout: u64,
        call: F,
    ) -> PyResult<sys::DDWAF_RET_CODE>
    where
        F: Ungil
            + Send
            + FnOnce(
                LibddwafPtr<R>,
                *mut sys::ddwaf_object,
                sys::ddwaf_allocator,
                *mut sys::ddwaf_object,
                u64,
            ) -> sys::DDWAF_RET_CODE,
    {
        let data_raw = data.raw;
        let result_raw = result.raw;
        let alloc = data.root.alloc;
        let status = py.detach(move || -> PyResult<_> {
            let _operation = operation
                .lock()
                .map_err(|_| PyRuntimeError::new_err("libddwaf context lock was poisoned"))?;
            Ok(call(
                context,
                data_raw.as_ptr(),
                alloc.as_ptr(),
                result_raw.as_ptr(),
                timeout,
            ))
        })?;
        if status != sys::DDWAF_ERR_INVALID_ARGUMENT {
            data.root.owned.store(false, Ordering::Release);
        }
        Ok(status)
    }

    #[pyfunction]
    fn ddwaf_builder_init() -> Option<DDWafBuilder> {
        LibddwafPtr::new(unsafe { sys::ddwaf_builder_init() }).map(|raw| DDWafBuilder { raw })
    }

    #[pyfunction]
    fn ddwaf_builder_add_or_update_config(
        builder: &DDWafBuilder,
        path: PyBackedBytes,
        config: &DDWafObject,
        diagnostics: &DDWafObject,
    ) -> PyResult<bool> {
        let path_len = byte_len(&path)?;
        Ok(unsafe {
            sys::ddwaf_builder_add_or_update_config(
                builder.raw.as_ptr(),
                bytes_address(&path),
                path_len,
                config.raw.as_ptr(),
                diagnostics.raw.as_ptr(),
            )
        })
    }

    #[pyfunction]
    fn ddwaf_builder_remove_config(builder: &DDWafBuilder, path: PyBackedBytes) -> PyResult<bool> {
        let path_len = byte_len(&path)?;
        Ok(unsafe {
            sys::ddwaf_builder_remove_config(builder.raw.as_ptr(), bytes_address(&path), path_len)
        })
    }

    #[pyfunction]
    fn ddwaf_builder_build_instance(builder: &DDWafBuilder) -> Option<DDWafHandle> {
        LibddwafPtr::new(unsafe { sys::ddwaf_builder_build_instance(builder.raw.as_ptr()) }).map(
            |raw| DDWafHandle {
                inner: Arc::new(HandleInner { raw }),
            },
        )
    }

    #[pyfunction]
    fn ddwaf_builder_get_config_paths(
        builder: &DDWafBuilder,
        filter: PyBackedBytes,
    ) -> PyResult<u32> {
        let filter_len = byte_len(&filter)?;
        Ok(unsafe {
            sys::ddwaf_builder_get_config_paths(
                builder.raw.as_ptr(),
                std::ptr::null_mut(),
                bytes_address(&filter),
                filter_len,
            )
        })
    }
}
