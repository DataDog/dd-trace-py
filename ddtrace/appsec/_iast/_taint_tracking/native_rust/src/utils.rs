use std::hash::Hasher;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple, PyString, PyByteArray, PyBytes};
use pyo3::PyResult;

pub fn calculate_hash(s: &str) -> u64 {
    let mut hasher = twox_hash::XxHash64::with_seed(0);
    hasher.write(s.as_bytes());
    hasher.finish()
}

fn process_flag_added_args(py: Python, 
    orig_function: &Bound<'_, PyAny>, 
    flag_added_args: i32, 
    args: &Bound<'_, PyTuple>, 
    kwargs: Option<&Bound<'_, PyDict>>) -> PyResult<PyObject> {
    let is_builtin_type = orig_function.is_none() 
        || orig_function.is_instance_of::<PyString>()
        || orig_function.is_instance_of::<PyByteArray>()
        || orig_function.is_instance_of::<PyBytes>();
    
    if is_builtin_type {
        // If orig_function is None or one of the built-in types, just return args for further processing
        Ok(args.into_py(py))
    } else {
        if flag_added_args > 0 {
            let num_args = args.len();
            if flag_added_args as usize <= num_args {
                // Collect into a vector of PyObjects first, then into a PyTuple
                let arg_vec: Vec<PyObject> = args.iter()
                    .skip(flag_added_args as usize)
                    .map(|item| item.to_object(py))
                    .collect();

                let sliced_args = PyTuple::new(py, arg_vec);

                // Call the original function with the sliced args and return its result
                orig_function.call(sliced_args, kwargs).map(|bound| bound.into_py(py))
            } else {
                // If flag_added_args is greater than the number of args, just return the original args
                Ok(args.into_py(py))
            }
        } else {
            // No slicing needed, call the original function with all args
            orig_function.call(args, kwargs).map(|bound| bound.into_py(py))
        }
    }
}
