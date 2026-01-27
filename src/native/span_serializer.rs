use std::{
    ops::DerefMut,
    ptr,
    sync::{Mutex},
};

use libdd_common::MutexExt;
use pyo3::{
    Bound, Py, PyAny, PyErr, PyResult, Python, pyclass, pymethods, sync::MutexExt as _, types::{
        PyAnyMethods, PyDict, PyDictMethods, PyFloat, PyFloatMethods, PyList, PyListMethods, PyModule, PyModuleMethods as _, PyString, PyStringMethods
    }
};

#[pyclass(frozen, module = "ddtrace.internal._native")]
pub struct SpanSerializer {
    buf: Mutex<libdd_trace_utils::msgpack_encoder::v04::TraceBuffer>,
    #[pyo3(get)]
    max_size: usize,
    #[pyo3(get)]
    max_item_size: usize,
    #[pyo3(get)]
    content_type: Py<PyString>,
}

#[pymethods]
impl SpanSerializer {
    #[new]
    fn new<'p>(py: Python<'p>, max_size: usize, max_item_size: usize) -> Self {
        Self {
            buf: Mutex::new(libdd_trace_utils::msgpack_encoder::v04::TraceBuffer::new(
                max_item_size,
                max_size,
            )),
            max_size,
            max_item_size,
            content_type: pyo3::intern!(py, "application/msgpack").clone().unbind(),
        }
    }

    fn __len__<'p>(&self, py: Python<'p>) -> usize {
        self.buf.lock_py_attached(py).unwrap().trace_count()
    }

    #[getter]
    fn size<'p>(&self, py: Python<'p>) -> usize {
        self.buf.lock_py_attached(py).unwrap().buffer_len()
    }

    fn encode<'p>(&self, py: Python<'p>) -> [(VecBuffer, usize); 1] {
        let mut buf = self.buf.lock_py_attached(py).unwrap();
        let trace_count = buf.trace_count();
        [(VecBuffer { buf: buf.flush() }, trace_count)]
    }

    fn put<'p>(&self, py_trace: Bound<'p, PyList>) -> PyResult<()> {
        let py = py_trace.py();
        let len = py_trace.len();
        let mut trace = Vec::with_capacity(len);
        for span in py_trace.iter() {
            let span = span.downcast::<crate::span::SpanData>()?;
            let mut span = span.borrow_mut();
            let span = span.deref_mut();
            for (k, v) in span._meta_struct.iter() {
                span.data
                    .meta_struct
                    .insert(k.clone_ref(py), serialize_meta_struct(v.bind(py))?.into());
            }
            trace.push(std::mem::take(&mut span.data));
        }
        py.allow_threads(|| {
            let mut buff = self.buf.lock_or_panic();
            buff.write_trace(&trace)
        })?;
        Ok(())
    }
}

#[pyo3::pyclass(frozen, module = "ddtrace.internal._native")]
/// Python bindings over a rust vector that implements the buffer protocol
struct VecBuffer {
    buf: Vec<u8>,
}

#[pyo3::pymethods]
impl VecBuffer {
    unsafe fn __getbuffer__(
        slf: Bound<'_, Self>,
        view: *mut pyo3::ffi::Py_buffer,
        flags: std::ffi::c_int,
    ) -> pyo3::PyResult<()> {
        if (flags & pyo3::ffi::PyBUF_WRITABLE) == pyo3::ffi::PyBUF_WRITABLE {
            return Err(pyo3::exceptions::PyBufferError::new_err(
                "Object is not writable",
            ));
        }
        let data = &slf.borrow().buf;
        unsafe {
            (*view).obj = slf.into_any().into_ptr();
            (*view).buf = data.as_ptr() as *mut std::ffi::c_void;
            (*view).len = data.len() as isize;
            (*view).readonly = 1;
            (*view).itemsize = 1;

            (*view).format = if (flags & pyo3::ffi::PyBUF_FORMAT) == pyo3::ffi::PyBUF_FORMAT {
                static FORMAT: &[u8] = b"B\0";
                FORMAT.as_ptr().cast_mut().cast()
            } else {
                ptr::null_mut()
            };

            (*view).ndim = 1;
            (*view).shape = if (flags & pyo3::ffi::PyBUF_ND) == pyo3::ffi::PyBUF_ND {
                &mut (*view).len
            } else {
                ptr::null_mut()
            };

            (*view).strides = if (flags & pyo3::ffi::PyBUF_STRIDES) == pyo3::ffi::PyBUF_STRIDES {
                &mut (*view).itemsize
            } else {
                ptr::null_mut()
            };

            (*view).suboffsets = ptr::null_mut();
            (*view).internal = ptr::null_mut();
        }
        Ok(())
    }
}

pub(crate) fn serialize_meta_struct<'p>(o: &Bound<'p, PyDict>) -> PyResult<Vec<u8>> {
    let mut buff = Vec::new();
    let _ = rmp::encode::write_map_len(&mut buff, o.len() as u32);
    for (k, v) in o.iter() {
        serialize_entry(&mut buff, &k, 0)?;
        serialize_entry(&mut buff, &v, 0)?;
    }
    Ok(buff)
}

fn serialize_entry<'p>(buff: &mut Vec<u8>, o: &Bound<'p, PyAny>, depth: usize) -> PyResult<()> {
    if depth > 100 {
        return Ok(());
    }
    if o.is_none() {
        let _ = rmp::encode::write_nil(buff);
    } else if let Ok(o) = o.downcast_exact::<PyString>() {
        let _ = rmp::encode::write_str(buff, o.to_str()?);
    } else if let Ok(o) = o.downcast_exact::<PyFloat>() {
        let _ = rmp::encode::write_f64(buff, o.value());
    } else if unsafe { pyo3::ffi::PyLong_CheckExact(o.as_ptr()) } == 1 {
        let l = unsafe { pyo3::ffi::PyLong_AsLongLong(o.as_ptr()) };
        PyErr::take(o.py()).map(Err).unwrap_or(Ok(()))?;
        let _ = rmp::encode::write_i64(buff, l);
    } else if let Ok(o) = o.downcast_exact::<PyList>() {
        let _ = rmp::encode::write_array_len(buff, o.len() as u32);
        for i in o.iter() {
            serialize_entry(buff, &i, depth + 1)?
        }
    } else if let Ok(o) = o.downcast_exact::<PyDict>() {
        let _ = rmp::encode::write_map_len(buff, o.len() as u32);
        for (k, v) in o.iter() {
            serialize_entry(buff, &k, depth + 1)?;
            serialize_entry(buff, &v, depth + 1)?;
        }
    }
    Ok(())
}

pub fn register_span_serializer(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanSerializer>()?;
    m.add_class::<VecBuffer>()?;
    Ok(())
}
