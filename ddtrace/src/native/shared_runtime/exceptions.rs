use libdd_shared_runtime::SharedRuntimeError as NativeSharedRuntimeError;
use pyo3::{create_exception, exceptions::PyException, prelude::*, PyErr};

create_exception!(
    shared_runtime_exceptions,
    SharedRuntimeError,
    PyException,
    "Shared runtime error"
);

pub fn shared_runtime_error_to_pyerr(error: NativeSharedRuntimeError) -> PyErr {
    SharedRuntimeError::new_err(error.to_string())
}

pub fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add(
        "SharedRuntimeError",
        m.py().get_type::<SharedRuntimeError>(),
    )?;
    Ok(())
}
