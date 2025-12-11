use std::{ops::DerefMut, sync::{Arc, Mutex}};

use ddcommon::MutexExt;
use pyo3::{
    types::{
        PyAnyMethods, PyDict, PyDictMethods, PyFloat, PyFloatMethods, PyList, PyListMethods,
        PyString, PyStringMethods,
    },
    Bound, PyAny, PyErr, PyResult,
};

use crate::span::SpanData;

struct NativeSpanSerializer {
    buff: Mutex<Vec<u8>>,
    nb_traces: usize,
}

impl NativeSpanSerializer {
    fn put<'p>(&mut self, py_trace: Bound<'p, PyList>) -> PyResult<()> {
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
        let buff = &self.buff;
        py.allow_threads(|| {
            let buff = buff.lock_or_panic();
            datadog_trace_utils::msgpack_encoder::v04::(slice, traces)
        });
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
