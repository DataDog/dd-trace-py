use std::{
    slice,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

use libddwaf_sys as sys;
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    pybacked::PyBackedBytes,
    pyclass, pyfunction, pymethods,
    types::{PyAny, PyAnyMethods, PyBool, PyBytes, PyDict, PyDictMethods, PyList, PyListMethods},
    IntoPyObject, Py, PyResult, Python,
};

use super::{byte_len, bytes_address, default_allocator, LibddwafPtr};

const MAX_TO_PYTHON_DEPTH: usize = 1_000;

pub(super) struct ObjectRoot {
    raw: LibddwafPtr<sys::ddwaf_object>,
    pub(super) alloc: LibddwafPtr<sys::_ddwaf_allocator>,
    pub(super) owned: AtomicBool,
}

impl ObjectRoot {
    fn new() -> PyResult<Arc<Self>> {
        let alloc = default_allocator()?;
        let raw = Box::into_raw(Box::new(sys::ddwaf_object::default()));
        Ok(Arc::new(Self {
            raw: LibddwafPtr::new(raw).expect("Box::into_raw never returns null"),
            alloc,
            owned: AtomicBool::new(true),
        }))
    }
}

impl Drop for ObjectRoot {
    fn drop(&mut self) {
        if *self.owned.get_mut() {
            unsafe { sys::ddwaf_object_destroy(self.raw.as_ptr(), self.alloc.as_ptr()) };
        }
        unsafe { drop(Box::from_raw(self.raw.as_ptr())) };
    }
}

#[pyclass(name = "DDWafObject", module = "appsec.libddwaf", frozen)]
pub(super) struct DDWafObject {
    pub(super) raw: LibddwafPtr<sys::ddwaf_object>,
    pub(super) root: Arc<ObjectRoot>,
}

impl DDWafObject {
    fn child(&self, raw: *mut sys::ddwaf_object) -> Option<Self> {
        LibddwafPtr::new(raw).map(|raw| Self {
            raw,
            root: Arc::clone(&self.root),
        })
    }
}

#[pymethods]
impl DDWafObject {
    #[new]
    fn new() -> PyResult<Self> {
        let root = ObjectRoot::new()?;
        Ok(Self {
            raw: root.raw,
            root,
        })
    }

    fn to_python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        object_to_python(py, self.raw.as_ptr(), 0)
    }
}

fn object_to_python(
    py: Python<'_>,
    raw: *const sys::ddwaf_object,
    depth: usize,
) -> PyResult<Py<PyAny>> {
    if depth > MAX_TO_PYTHON_DEPTH {
        return Err(PyValueError::new_err(
            "DDWafObject exceeds the maximum conversion depth",
        ));
    }

    match unsafe { sys::ddwaf_object_get_type(raw) } {
        sys::DDWAF_OBJ_INVALID | sys::DDWAF_OBJ_NULL => Ok(py.None()),
        sys::DDWAF_OBJ_BOOL => {
            let value = unsafe { sys::ddwaf_object_get_bool(raw) };
            Ok(PyBool::new(py, value).to_owned().into_any().unbind())
        }
        sys::DDWAF_OBJ_SIGNED => {
            let value = unsafe { sys::ddwaf_object_get_signed(raw) };
            Ok(value.into_pyobject(py)?.into_any().unbind())
        }
        sys::DDWAF_OBJ_UNSIGNED => {
            let value = unsafe { sys::ddwaf_object_get_unsigned(raw) };
            Ok(value.into_pyobject(py)?.into_any().unbind())
        }
        sys::DDWAF_OBJ_FLOAT => {
            let value = unsafe { sys::ddwaf_object_get_float(raw) };
            Ok(value.into_pyobject(py)?.into_any().unbind())
        }
        sys::DDWAF_OBJ_STRING | sys::DDWAF_OBJ_LITERAL_STRING | sys::DDWAF_OBJ_SMALL_STRING => {
            let mut length = 0;
            let value = unsafe { sys::ddwaf_object_get_string(raw, &mut length) };
            if length > isize::MAX as usize {
                return Err(PyValueError::new_err(
                    "libddwaf returned an oversized string",
                ));
            }
            let bytes = if value.is_null() || length == 0 {
                &[]
            } else {
                unsafe { slice::from_raw_parts(value.cast::<u8>(), length) }
            };
            Ok(PyBytes::new(py, bytes)
                .call_method1("decode", ("UTF-8", "ignore"))?
                .unbind())
        }
        sys::DDWAF_OBJ_ARRAY => {
            let list = PyList::empty(py);
            for index in 0..unsafe { sys::ddwaf_object_get_size(raw) } {
                let child = unsafe { sys::ddwaf_object_at_value(raw, index) };
                if child.is_null() {
                    return Err(PyRuntimeError::new_err(
                        "libddwaf returned a null array element",
                    ));
                }
                list.append(object_to_python(py, child, depth + 1)?)?;
            }
            Ok(list.into_any().unbind())
        }
        sys::DDWAF_OBJ_MAP => {
            let dict = PyDict::new(py);
            for index in 0..unsafe { sys::ddwaf_object_get_size(raw) } {
                let key = unsafe { sys::ddwaf_object_at_key(raw, index) };
                let value = unsafe { sys::ddwaf_object_at_value(raw, index) };
                if key.is_null() || value.is_null() {
                    return Err(PyRuntimeError::new_err(
                        "libddwaf returned a null map entry",
                    ));
                }
                dict.set_item(
                    object_to_python(py, key, depth + 1)?,
                    object_to_python(py, value, depth + 1)?,
                )?;
            }
            Ok(dict.into_any().unbind())
        }
        _ => Ok(py.None()),
    }
}

#[pyfunction]
pub(super) fn ddwaf_object_set_string(
    object: &DDWafObject,
    string: PyBackedBytes,
) -> PyResult<bool> {
    let length = byte_len(&string)?;
    let raw = unsafe {
        sys::ddwaf_object_set_string(
            object.raw.as_ptr(),
            bytes_address(&string),
            length,
            object.root.alloc.as_ptr(),
        )
    };
    Ok(!raw.is_null())
}

fn set_scalar<F>(object: &DDWafObject, call: F) -> bool
where
    F: FnOnce(*mut sys::ddwaf_object) -> *mut sys::ddwaf_object,
{
    !call(object.raw.as_ptr()).is_null()
}

#[pyfunction]
pub(super) fn ddwaf_object_set_signed(object: &DDWafObject, value: i64) -> bool {
    set_scalar(object, |raw| unsafe {
        sys::ddwaf_object_set_signed(raw, value)
    })
}

#[pyfunction]
pub(super) fn ddwaf_object_set_bool(object: &DDWafObject, value: bool) -> bool {
    set_scalar(object, |raw| unsafe {
        sys::ddwaf_object_set_bool(raw, value)
    })
}

#[pyfunction]
pub(super) fn ddwaf_object_set_float(object: &DDWafObject, value: f64) -> bool {
    set_scalar(object, |raw| unsafe {
        sys::ddwaf_object_set_float(raw, value)
    })
}

#[pyfunction]
pub(super) fn ddwaf_object_set_null(object: &DDWafObject) -> bool {
    set_scalar(object, |raw| unsafe { sys::ddwaf_object_set_null(raw) })
}

fn set_container<F>(object: &DDWafObject, capacity: u16, call: F) -> bool
where
    F: FnOnce(*mut sys::ddwaf_object, u16, sys::ddwaf_allocator) -> *mut sys::ddwaf_object,
{
    !call(object.raw.as_ptr(), capacity, object.root.alloc.as_ptr()).is_null()
}

#[pyfunction]
pub(super) fn ddwaf_object_set_array(object: &DDWafObject, capacity: u16) -> bool {
    set_container(object, capacity, |raw, capacity, alloc| unsafe {
        sys::ddwaf_object_set_array(raw, capacity, alloc)
    })
}

#[pyfunction]
pub(super) fn ddwaf_object_set_map(object: &DDWafObject, capacity: u16) -> bool {
    set_container(object, capacity, |raw, capacity, alloc| unsafe {
        sys::ddwaf_object_set_map(raw, capacity, alloc)
    })
}

#[pyfunction]
pub(super) fn ddwaf_object_insert(array: &DDWafObject) -> Option<DDWafObject> {
    array.child(unsafe { sys::ddwaf_object_insert(array.raw.as_ptr(), array.root.alloc.as_ptr()) })
}

#[pyfunction]
pub(super) fn ddwaf_object_insert_key(
    map: &DDWafObject,
    key: PyBackedBytes,
) -> PyResult<Option<DDWafObject>> {
    let length = byte_len(&key)?;
    Ok(map.child(unsafe {
        sys::ddwaf_object_insert_key(
            map.raw.as_ptr(),
            bytes_address(&key),
            length,
            map.root.alloc.as_ptr(),
        )
    }))
}

#[pyfunction]
pub(super) fn ddwaf_object_from_json(
    py: Python<'_>,
    output: &DDWafObject,
    json_str: PyBackedBytes,
) -> PyResult<bool> {
    let length = byte_len(&json_str)?;
    let raw = output.raw;
    let alloc = output.root.alloc;
    let success = py.detach(move || unsafe {
        sys::ddwaf_object_from_json(
            raw.as_ptr(),
            bytes_address(&json_str),
            length,
            alloc.as_ptr(),
        )
    });
    if !success {
        output.root.owned.store(false, Ordering::Release);
    }
    Ok(success)
}
