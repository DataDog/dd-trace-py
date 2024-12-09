use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyTuple};
use crate::initializer;
use crate::utils;
use crate::get_ranges;
use crate::set_ranges;
use pyo3::types::PyString;

#[pyfunction]
#[pyo3(signature = (orig_function=None, flag_added_args=0, *args, **kwargs))]
pub fn aspect_lower(
    py: Python,
    orig_function: Option<&Bound<'_, PyAny>>,
    flag_added_args: i32,
    args: &Bound<'_, PyTuple>,
    kwargs: Option<&Bound<'_, PyDict>>,
) -> PyResult<PyObject> {
    // Call process_flag_added_args
    let result_or_args = utils::process_flag_added_args(py, orig_function, flag_added_args, args, kwargs)?;
    let lower_func = "lower";

    // Check if result_or_args is a tuple
    if let Ok(args_tuple) = result_or_args.extract::<Bound<'_, PyTuple>>() {
        // Get text from args_tuple[0]
        let text = args_tuple.get_item(0)?;
        let text_str = text.as_any();

        // Get sliced_args
        let sliced_args = if args.len() > 1 {
            PyTuple::new(py, &args.as_slice()[1..])?
        } else {
            PyTuple::empty(py)
        };

        // Call the lower_func method on text
        let result_o = text_str.call_method(lower_func, sliced_args, kwargs)?;

        // Check if the tx_map is empty
        let initializer = initializer::get_initializer();
        if initializer.is_empty() {
            return Ok(result_o.into());
        }

        // Try-catch equivalent in Rust
        let tainting_result = (|| -> PyResult<()> {
            // Get ranges for text
            let py_string: &Bound<'_, PyString> = text_str.downcast::<PyString>()?;

            if let Some(ranges) = get_ranges(py, py_string.to_str().unwrap())? {
                let taint_ranges = ranges;
                if !taint_ranges.is_empty() {
                    // Set ranges on result_o
                    set_ranges(py, &result_o.clone().downcast::<PyString>()?.to_string(), &taint_ranges)?;
                }
            }
            Ok(())
        })();

        // Handle any errors (ignore them as in the C++ code)
        if tainting_result.is_err() {
            // Log or handle the error if needed
        }

        Ok(result_o.into())
    } else {
        // Return result_or_args as is
        Ok(result_or_args.into())
    }
}