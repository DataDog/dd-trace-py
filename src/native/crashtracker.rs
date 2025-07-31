use pyo3::exceptions::PyException;
use pyo3::prelude::*;

// We expose the ddog_crasht_receiver_entry_point_stdin function to use from Python command,
// _dd_crashtracker_receiver. This is to avoid dealing with undefined symbols when linking _native.so
// with crashtracker_exe, which was built from a cpp file. Also, this has an advantage that we no
// longer need to worry about platform specific binary names for crashtracker receiver
#[pyfunction(name = "crashtracker_receiver")]
pub fn crashtracker_receiver() -> PyResult<()> {
    let result = unsafe { datadog_profiling_ffi::ddog_crasht_receiver_entry_point_stdin() };
    match result {
        datadog_profiling_ffi::VoidResult::Ok => Ok(()),
        datadog_profiling_ffi::VoidResult::Err(e) => {
            let err_msg = format!("Failed to start crashtracker receiver: {e:?}");
            Err(PyException::new_err(err_msg))
        }
    }
}
