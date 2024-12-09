use std::hash::Hasher;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple, PyString, PyByteArray, PyBytes};
use pyo3::PyResult;

pub fn calculate_hash(s: &str) -> u64 {
    let mut hasher = twox_hash::XxHash64::with_seed(0);
    hasher.write(s.as_bytes());
    hasher.finish()
}

pub fn process_flag_added_args<'a>(
    py: Python<'a>,
    orig_function: Option<&'a Bound<'a, PyAny>>,
    flag_added_args: i32,
    args: &'a Bound<'a, PyTuple>,
    kwargs: Option<&'a Bound<'a, PyDict>>,
) -> PyResult<Bound<'a, PyAny>> {
    let is_builtin_type = orig_function
        .map_or(true, |func| {
            func.is_instance_of::<PyString>()
                || func.is_instance_of::<PyByteArray>()
                || func.is_instance_of::<PyBytes>()
        });

    if is_builtin_type {
        // If orig_function is None or one of the built-in types, just return args for further processing
        return Ok(args.as_any().clone());
    }

    if flag_added_args > 0 {
        let num_args = args.len();
        if flag_added_args as usize <= num_args {
            // Collect into a vector of PyObjects
            let arg_vec: Vec<PyObject> = args
                .iter()
                .skip(flag_added_args as usize)
                .map(|item| item.into())
                .collect();

            // Create a new PyTuple
            let sliced_args = PyTuple::new(py, arg_vec)?;

            // Call the original function with the sliced args and return its result
            if let Some(orig_func) = orig_function {
                return orig_func.call(sliced_args, kwargs);
            }
        }
    }

    // No slicing needed, call the original function with all args
    if let Some(orig_func) = orig_function {
        return orig_func.call(args, kwargs);
    }

    // If no orig_function is provided, just return args
    Ok(args.as_any().clone())
}